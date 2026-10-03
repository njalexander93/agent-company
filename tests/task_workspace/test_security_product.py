"""Independent Product Security regressions using small disposable local stores.

No host activation, provider calls, or large-capacity probes are performed.
"""
from unittest import mock

from test_lifecycle import Fixture, w


class ProductSecurityTests(Fixture):
    def test_terminal_outcome_retains_active_state_until_pending_tool_settles(self):
        self.create()
        self.ready()
        self.call('tool-start', tool_id='pending-fixture')
        before = self.state()
        events_before = (self.root / '.task/TEST-1/events.jsonl').read_bytes()
        result = w.execute(self.req('outcome', expected_revision=before['revision'],
                                    disposition='cancelled', evidence=self.evidence()))
        self.assertEqual(result['code'], 'PENDING_OPERATION')
        self.assertEqual(self.state(), before)
        self.assertEqual((self.root / '.task/TEST-1/events.jsonl').read_bytes(), events_before)
        self.call('tool-complete', tool_id='pending-fixture', completed=True)
        self.assertFalse(self.state()['participants'][self.base['coordinator']]['pending'])
        self.call('outcome', expected_revision=self.state()['revision'],
                  disposition='cancelled', evidence=self.evidence())
        self.assertEqual(self.state()['disposition'], 'cancelled')

    def test_packet_reassignment_does_not_revive_detached_generation(self):
        self.create()
        worker = w.participant_key({**self.base, 'session_id': 'worker'})
        self.call('scope', expected_revision=self.state()['revision'],
                  target_participant=worker, packet=[], owned_paths=['context/worker.md'])
        attached = self.call('attach', session_id='worker', binding_generation=None)
        old_generation = attached['binding_generation']
        self.call('detach', session_id='worker', binding_generation=old_generation,
                  evidence=self.evidence())
        self.call('scope', expected_revision=self.state()['revision'],
                  target_participant=worker, packet=[])
        self.assertEqual(self.state()['participants'][worker]['status'], 'detached')
        request = self.req('update', session_id='worker', binding_generation=old_generation,
                           expected_revision=self.state()['revision'], path='context/worker.md',
                           old_digest=None, content='stale generation must not publish',
                           provenance={'sources': self.evidence(), 'applicability': 'test', 'status': 'draft'})
        self.assertEqual(w.execute(request)['code'], 'STALE_BINDING')
        self.assertFalse((self.root / '.task/TEST-1/context/worker.md').exists())
        resumed = self.call('attach', session_id='worker', binding_generation=old_generation)
        self.assertGreater(resumed['binding_generation'], old_generation)
        request['expected_revision'] = self.state()['revision']
        self.assertFalse(w.execute(request)['ok'])

    def test_non_event_operations_cannot_persist_supplied_event_code(self):
        marker = 'SYNTHETIC_PRIVATE_TOOL_OUTPUT'
        rejected = w.execute(self.req('create', event={'code': marker}))
        self.assertEqual(rejected['code'], 'INVALID_REQUEST')
        self.assertFalse((self.root / '.task/TEST-1').exists())
        self.create()
        before = (self.root / '.task/TEST-1/events.jsonl').read_bytes()
        rejected = w.execute(self.req('scope', expected_revision=self.state()['revision'],
                                     target_participant=self.base['coordinator'], packet=[],
                                     event={'code': marker}))
        self.assertEqual(rejected['code'], 'INVALID_REQUEST')
        self.assertEqual((self.root / '.task/TEST-1/events.jsonl').read_bytes(), before)
        self.call('event', event_type='observation', event={'code': 'UNKNOWN'})
        self.assertNotIn(marker.encode(), (self.root / '.task/TEST-1/events.jsonl').read_bytes())

    def test_small_patched_export_cap_rejects_before_export_publication(self):
        self.create()
        state = self.state()
        content = 'bounded synthetic archive bytes\n' * 300
        self.call('update', expected_revision=state['revision'], path='roadmap.md',
                  old_digest=state['files']['roadmap.md'], content=content,
                  provenance={'sources': self.evidence(), 'applicability': 'test', 'status': 'draft'})
        request = self.req('archive-prepare', expected_revision=self.state()['revision'])
        control = self.root / '.task/.control/issues/TEST-1'
        with mock.patch.object(w, 'MAX_REQUEST', 16 * 1024):
            result = w.execute(request)
        self.assertEqual(result['code'], 'ARCHIVE_PENDING', result)
        self.assertFalse(list(control.glob('export-*.json')))
        self.assertFalse((control / 'transaction.json').exists())
        self.assertEqual((self.root / '.task/TEST-1/roadmap.md').read_text(), content)
        # The normal-cap retry remains possible after the preparation event committed.
        retried = self.require_ok(w.execute(request))
        self.assertTrue(retried['parts'])
        self.assertEqual(retried, w.execute(request))
