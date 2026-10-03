"""Independent contract probes. All payloads live in disposable Git repositories.

Provider observations are fixtures: these tests make no provider/desktop acceptance claim.
"""
import copy
import json
from pathlib import Path
import shlex
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import Fixture, w, hook


class IndependentContractTests(Fixture):
    def update_request(self):
        state = self.state()
        return self.req('update', path='roadmap.md', content='# committed replacement\n',
                        expected_revision=state['revision'], old_digest=state['files']['roadmap.md'],
                        provenance={'sources': self.evidence(), 'status': 'draft', 'applicability': 'test'})

    def test_T02_foreign_clone_registration_preserves_canonical_payload(self):
        self.create()
        foreign = Path(self.temp.name).resolve() / 'foreign'
        self.git('clone', '-q', str(self.root), str(foreign))
        (foreign / '.task').mkdir(mode=0o700)
        (foreign / '.task/.repository.json').write_bytes((self.root / '.task/.repository.json').read_bytes())
        before = (self.root / '.task/TEST-1/roadmap.md').read_bytes()
        result = w.execute(self.req('resume', worktree=str(foreign)))
        self.assertEqual(result['code'], 'REPOSITORY_MISMATCH', result)
        self.assertEqual((self.root / '.task/TEST-1/roadmap.md').read_bytes(), before)

    def test_T03_detach_resume_fences_old_generation_and_preserves_progress(self):
        self.create()
        self.require_ok(w.execute(self.update_request()))
        before = (self.root / '.task/TEST-1/roadmap.md').read_bytes()
        old_generation = self.base['binding_generation']
        self.call('detach', evidence=self.evidence())
        resumed = self.call('resume')
        self.assertGreater(resumed['binding_generation'], old_generation)
        self.assertFalse(w.execute(self.update_request())['ok'])
        self.assertEqual((self.root / '.task/TEST-1/roadmap.md').read_bytes(), before)

    def test_T04_participant_cannot_update_coordinator_roadmap(self):
        self.create()
        member = self.req('attach', session_id='reviewer', binding_generation=None)
        key = w.participant_key(member)
        self.call('scope', expected_revision=self.state()['revision'], target_participant=key, packet=[])
        generation = self.require_ok(w.execute(member))['binding_generation']
        request = self.update_request()
        request.update(session_id='reviewer', binding_generation=generation)
        self.assertEqual(w.execute(request)['code'], 'NOT_OWNER')

    def test_T05_recovery_preserves_foreign_edit_when_roadmap_disappears(self):
        self.create()
        request = self.update_request()
        def crash(point):
            if point == 'intent':
                raise OSError('injected crash before publication')
        with mock.patch.object(w, 'FAILPOINT', crash):
            self.assertFalse(w.execute(request)['ok'])
        payload = self.root / '.task/TEST-1'
        (payload / 'roadmap.md').unlink()
        sentinel = b'FOREIGN-EDIT-MUST-SURVIVE\n'
        (payload / 'events.jsonl').write_bytes(sentinel)
        result = w.execute(request)
        self.assertFalse(result['ok'], 'Recovery must stop before overwriting changed bytes')
        self.assertEqual((payload / 'events.jsonl').read_bytes(), sentinel)

    def test_T06_optional_observation_cap_is_suppressed_and_counted(self):
        self.create()
        before = (self.root / '.task/TEST-1/events.jsonl').read_bytes()
        with mock.patch.object(w, 'MAX_EVENTS', len(before)):
            result = w.execute(self.req('event', event_type='observation', event={'code': 'OK'}))
        self.assertTrue(result['ok'], 'Contract requires optional loss accounting instead of required-write refusal')
        self.assertEqual((self.root / '.task/TEST-1/events.jsonl').read_bytes(), before)
        self.assertGreater(self.state().get('optional_loss_count', 0), 0)

    def test_T07_scope_change_invalidates_acknowledgement(self):
        self.create()
        self.ready()
        self.call('ready')
        self.call('scope', expected_revision=self.state()['revision'], target_participant=self.base['coordinator'], packet=[])
        self.assertEqual(w.execute(self.req('ready'))['code'], 'NOT_READY')

    def test_T08_async_handle_retains_participant_after_session_end(self):
        self.create()
        self.ready()
        self.call('tool-start', tool_id='async-tool')
        self.call('tool-complete', tool_id='async-tool', async_handle='remote-process-17', completed=False)
        for event in ('Stop', 'Interrupt', 'SessionEnd'):
            hook.handle({'hook_event_name': event, 'cwd': str(self.root), 'session_id': 'coordinator'})
        self.assertEqual(w.execute(self.req('detach', evidence=self.evidence()))['code'], 'PENDING_OPERATION')
        self.assertIn('async-tool', self.state()['participants'][self.base['coordinator']]['pending'])
        self.assertTrue((self.root / '.task/TEST-1').is_dir())

    def test_T09_changed_local_bytes_after_plan_are_retained(self):
        self.create()
        observations = self.archive()
        cleanup = self.cleanup_request(observations)
        path = self.root / '.task/TEST-1/roadmap.md'
        path.write_text('UNTRACKED-CHANGE-MUST-SURVIVE\n')
        self.assertEqual(w.execute(cleanup)['code'], 'UNTRACKED_CHANGE')
        self.assertEqual(path.read_text(), 'UNTRACKED-CHANGE-MUST-SURVIVE\n')

    def test_T10_partial_cleanup_recovery_preserves_other_issue(self):
        self.create()
        observations = self.archive()
        cleanup = self.cleanup_request(observations)
        sentinel = self.root / '.task/OTHER-7'
        sentinel.mkdir()
        (sentinel / 'keep').write_text('outside cleanup target')
        def crash(point):
            if point == 'delete:events.jsonl':
                raise OSError('injected partial deletion')
        with mock.patch.object(w, 'FAILPOINT', crash):
            self.assertFalse(w.execute(cleanup)['ok'])
        self.require_ok(w.execute(cleanup))
        self.assertEqual(self.state()['storage'], 'cleaned')
        self.assertEqual((sentinel / 'keep').read_text(), 'outside cleanup target')

    def test_T11_corrupt_or_unavailable_archive_never_recreates_empty_issue(self):
        self.create()
        observations = self.archive()
        self.require_ok(w.execute(self.cleanup_request(observations)))
        corrupt = copy.deepcopy(observations)
        corrupt[0]['content'] += '\nCORRUPTED'
        # Change the encoded archive bytes, not merely its human-readable heading.
        corrupt[0]['content'] = corrupt[0]['content'].replace('eyJ', 'eyK', 1)
        for source in ([], observations[1:], corrupt):
            result = w.execute(self.req('restore', observations=source))
            self.assertFalse(result['ok'], result)
            self.assertFalse((self.root / '.task/TEST-1').exists())
            self.assertEqual(self.state()['storage'], 'cleaned')

    def test_T14_bootstrap_session_issue_and_shell_injection_gate(self):
        hook.handle({'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.root),
                     'session_id': 'coordinator', 'prompt': 'Task: TEST-1'})
        event = {'cwd': str(self.root), 'session_id': 'coordinator', 'tool_name': 'exec_command'}
        request = self.req('create')
        for changes in ({'session_id': 'foreign'}, {'issue_id': 'TEST-2'}, {'operation': 'update'}):
            changed = {**request, **changes}
            event['tool_input'] = {'cmd': shlex.join([hook.PYTHON, str(hook.LIFECYCLE), '--request-json', json.dumps(changed)]), 'login': False}
            self.assertFalse(hook.bootstrap(event))
        command = shlex.join([hook.PYTHON, str(hook.LIFECYCLE), '--request-json', json.dumps(request)])
        for bad in (command + '\ntrue', '(' + command + ')', command + ' | cat', command + ' &', 'X=1 ' + command):
            event['tool_input'] = {'cmd': bad, 'login': False}
            self.assertFalse(hook.bootstrap(event))
        self.assertEqual(hook.handle({'hook_event_name': 'PermissionRequest'}), {})
