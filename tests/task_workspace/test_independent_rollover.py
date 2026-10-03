"""Independent rollover probes using temporary repositories and provider fixtures."""
import json
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import Fixture, w


class IndependentRolloverTests(Fixture):
    def checkpoint_archive(self):
        export = self.call('archive-prepare', expected_revision=self.state()['revision'])
        observations = self.observations(export)
        index = self.call('archive-index', observations=observations)
        observations.append({'id': 'index', 'url': 'https://linear.app/test/document/index',
                             'issue': self.base['issue_uuid'], 'updatedAt': '2026-10-03T00:00:00Z',
                             'origin': 'linear_get_document', 'request_id': 'fixture-get', 'content': index['content']})
        self.call('archive-verify', observations=observations)
        return observations

    def rollover(self):
        return self.call('event-rollover', expected_revision=self.state()['revision'])

    def assert_history(self):
        payload = self.root / '.task/TEST-1'
        paths = sorted(payload.glob('events-*.jsonl')) + [payload / 'events.jsonl']
        sequence, head = 0, None
        for path in paths:
            data = path.read_bytes()
            self.assertTrue(data.endswith(b'\n'))
            for line in data.splitlines():
                event = json.loads(line)
                digest = event.pop('digest')
                sequence += 1
                self.assertEqual(event['seq'], sequence)
                self.assertEqual(event['prev_digest'], head)
                self.assertEqual(w.sha(w.canonical(event)), digest)
                head = digest
        self.assertEqual((sequence, head), (self.state()['seq'], self.state()['head']))

    def test_rollover_requires_current_verified_archive_and_revision(self):
        self.create()
        before = self.state()
        self.assertEqual(w.execute(self.req('event-rollover', expected_revision=before['revision']))['code'], 'ARCHIVE_PENDING')
        self.checkpoint_archive()
        self.assertEqual(w.execute(self.req('event-rollover', expected_revision=before['revision']))['code'], 'REVISION_CONFLICT')
        self.call('event', event_type='check', event={'code': 'OK'})
        self.assertEqual(w.execute(self.req('event-rollover', expected_revision=self.state()['revision']))['code'], 'ARCHIVE_PENDING')
        self.assertEqual(list((self.root / '.task/TEST-1').glob('events-*.jsonl')), [])

    def test_non_coordinator_cannot_rollover(self):
        self.create()
        member = self.req('attach', session_id='reviewer', binding_generation=None)
        key = w.participant_key(member)
        self.call('scope', expected_revision=self.state()['revision'], target_participant=key, packet=[])
        generation = self.require_ok(w.execute(member))['binding_generation']
        self.checkpoint_archive()
        denied = w.execute(self.req('event-rollover', session_id='reviewer', binding_generation=generation,
                                    expected_revision=self.state()['revision']))
        self.assertEqual(denied['code'], 'NOT_OWNER')

    def test_crash_retry_all_publication_boundaries_and_global_chain(self):
        self.create()
        for point in ('before-intent', 'intent', 'payload:events.jsonl', 'payload:segment', 'payload-flush', 'state', 'transaction-complete'):
            with self.subTest(point=point):
                self.checkpoint_archive()
                payload = self.root / '.task/TEST-1'
                old = (payload / 'events.jsonl').read_bytes()
                prior_sequence = self.state()['seq']
                request = self.req('event-rollover', expected_revision=self.state()['revision'])
                def crash(actual):
                    if actual == point or (point == 'payload:segment' and actual.startswith('payload:events-')):
                        raise OSError('injected rollover crash')
                with mock.patch.object(w, 'FAILPOINT', crash):
                    self.assertFalse(w.execute(request)['ok'])
                result = self.require_ok(w.execute(request))
                self.assertEqual(w.execute(request), result)
                self.assertEqual((payload / result['segment']).read_bytes(), old)
                self.assertEqual(self.state()['seq'], prior_sequence + 1)
                self.assert_history()

    def test_rollover_history_survives_cleanup_and_restore(self):
        self.create()
        retained = {}
        for _ in range(2):
            self.checkpoint_archive()
            result = self.rollover()
            retained[result['segment']] = (self.root / '.task/TEST-1' / result['segment']).read_bytes()
        observations = self.archive()
        self.require_ok(w.execute(self.cleanup_request(observations)))
        self.assertFalse((self.root / '.task/TEST-1').exists())
        restored = self.call('restore', observations=observations)
        self.assertGreater(restored['binding_generation'], self.base['binding_generation'])
        for path, data in retained.items():
            self.assertEqual((self.root / '.task/TEST-1' / path).read_bytes(), data)
        self.assertEqual(len(self.state()['event_segments']), 2)
        self.assert_history()

    def test_archive_reserve_allows_checkpoint_and_rollover_when_required_event_full(self):
        self.create()
        active = self.root / '.task/TEST-1/events.jsonl'
        before = active.read_bytes()
        with mock.patch.object(w, 'MAX_EVENTS', len(before) + 8192 + 512):
            self.assertEqual(w.execute(self.req('event', event_type='check', event={'code': 'OK'}))['code'], 'ARCHIVE_PENDING')
            self.assertEqual(active.read_bytes(), before)
            self.checkpoint_archive()
            self.rollover()
        self.assert_history()

    def test_optional_observation_respects_checkpoint_reserve(self):
        self.create()
        active = self.root / '.task/TEST-1/events.jsonl'
        before = active.read_bytes()
        with mock.patch.object(w, 'MAX_EVENTS', len(before) + 8193):
            result = w.execute(self.req('event', event_type='observation', event={'code': 'OK'}))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['code'], 'OPTIONAL_SUPPRESSED')
        self.assertEqual(active.read_bytes(), before)
        self.assertEqual(self.state()['optional_loss_count'], 1)

    def test_scoped_read_excludes_unassigned_segments(self):
        self.create()
        self.checkpoint_archive()
        segment = self.rollover()['segment']
        member = self.req('attach', session_id='reviewer', binding_generation=None)
        key = w.participant_key(member)
        self.call('scope', expected_revision=self.state()['revision'], target_participant=key, packet=[])
        generation = self.require_ok(w.execute(member))['binding_generation']
        result = self.call('read', session_id='reviewer', binding_generation=generation)
        self.assertEqual(result['references'], [])
        self.assertNotIn(segment, json.dumps(result))
        packet = [{'id': 'archived-events', 'locator': segment,
                   'sha256': w.sha((self.root / '.task/TEST-1' / segment).read_bytes()), 'required': True,
                   'authority': 'diagnostic', 'reason': 'review', 'stage': 'review', 'reader': key}]
        self.call('scope', expected_revision=self.state()['revision'], target_participant=key, packet=packet)
        allowed = self.call('read', session_id='reviewer', binding_generation=generation)
        self.assertEqual(len(allowed['references']), 1)
        self.assertEqual(allowed['references'][0]['locator'], segment)
        self.assertTrue(allowed['references'][0]['available'])

    def test_near_cap_tool_admission_denies_without_leaving_pending_work(self):
        self.create()
        self.ready()
        active = self.root / '.task/TEST-1/events.jsonl'
        before = active.read_bytes()
        with mock.patch.object(w, 'MAX_EVENTS', len(before) + 8192 + 512):
            result = w.execute(self.req('tool-start', tool_id='cannot-finish'))
            self.assertEqual(result['code'], 'ARCHIVE_PENDING')
            self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})
            self.assertEqual(active.read_bytes(), before)
            self.checkpoint_archive()
            self.rollover()
        self.assert_history()

    def test_admitted_async_tool_can_transition_complete_and_archive_at_cap(self):
        self.create()
        self.ready()
        active = self.root / '.task/TEST-1/events.jsonl'
        cap = len(active.read_bytes()) + 3 * 8192 + 1024
        def exhaust_ordinary_events():
            for _ in range(100):
                result = w.execute(self.req('event', event_type='check', event={'code': 'OK'}))
                if not result['ok']:
                    self.assertEqual(result['code'], 'ARCHIVE_PENDING')
                    return
            self.fail('Expected bounded fixture to reach event admission cap')
        with mock.patch.object(w, 'MAX_EVENTS', cap):
            self.call('tool-start', tool_id='admitted')
            exhaust_ordinary_events()
            self.call('tool-complete', tool_id='admitted', async_handle='handle-a', completed=False)
            exhaust_ordinary_events()
            before = active.read_bytes()
            revision = self.state()['revision']
            for _ in range(3):
                self.call('tool-complete', tool_id='admitted', async_handle='handle-a', completed=False)
            self.assertEqual(active.read_bytes(), before)
            self.assertEqual(self.state()['revision'], revision)
            changed = w.execute(self.req('tool-complete', tool_id='admitted', async_handle='handle-b', completed=False))
            self.assertEqual(changed['code'], 'REQUEST_CONFLICT')
            self.assertEqual(active.read_bytes(), before)
            self.call('tool-complete', tool_id='admitted', completed=True)
            self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})
            self.checkpoint_archive()
            self.rollover()
        self.assert_history()
