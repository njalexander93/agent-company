"""Author regression tests for independently reported findings; not independent review."""
import copy
import json
from pathlib import Path
import shlex
from unittest import mock
from test_lifecycle import Fixture, w, hook


class ReviewRegressions(Fixture):
    def test_automatic_creation_from_explicit_startup_assignment(self):
        self.call('register', repo_id=None, main_worktree=str(self.root), startup={
            'issue_id': 'TEST-1', 'issue_uuid': self.base['issue_uuid'],
            'coordinator': self.base['coordinator'], 'packet': []})
        event = {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.root),
                 'session_id': 'coordinator', 'prompt': 'Task: TEST-1'}
        hook.handle(event)
        self.assertTrue((self.root / '.task/TEST-1/roadmap.md').is_file())
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['packet'], [])
        before = (self.root / '.task/TEST-1/events.jsonl').read_bytes()
        hook.handle(event)
        self.assertEqual(before, (self.root / '.task/TEST-1/events.jsonl').read_bytes())

    def test_checkpoint_and_provenance_survive_archive_restore(self):
        self.create()
        checkpoint = {'goal': 'unique-goal', 'constraints': ['unique-constraint'], 'sources': self.evidence(),
                      'progress': 'unique-progress', 'blockers': 'unique-blocker', 'handoff': 'unique-handoff', 'candidate': 'test-only'}
        self.call('checkpoint', expected_revision=self.state()['revision'], checkpoint=checkpoint)
        observations = self.archive()
        self.require_ok(w.execute(self.cleanup_request(observations)))
        self.call('restore', observations=observations)
        self.assertEqual(self.state()['checkpoint'], checkpoint)

    def test_completion_requires_source_bytes_and_provider_status(self):
        self.create()
        completion = {name: self.evidence() for name in ['human_acceptance', 'merge', 'obligations']}
        result = w.execute(self.req('outcome', expected_revision=self.state()['revision'], disposition='completed',
                                   evidence=self.evidence(), completion=completion))
        self.assertEqual(result['code'], 'EVIDENCE_REQUIRED')
        self.assertEqual(self.state()['disposition'], 'active')

    def test_explicit_reconciliation_imports_only_inspected_markdown(self):
        self.create()
        path = self.root / '.task/TEST-1/roadmap.md'
        path.write_text('# inspected external edit\n')
        files = {p: w.sha((self.root / '.task/TEST-1' / p).read_bytes()) for p in self.state()['files']}
        self.call('reconcile-files', expected_revision=self.state()['revision'], inventory=files, evidence=self.evidence())
        self.assertEqual(path.read_text(), '# inspected external edit\n')
        self.call('diagnose')

    def test_bootstrap_rejects_missing_login_shell_override_and_unknown_fields(self):
        event = {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.root), 'session_id': 'coordinator', 'prompt': 'Task: TEST-1'}
        hook.handle(event)
        request = self.req('diagnose', repo_id=None, issue_id=None, issue_uuid=None, coordinator=None)
        command = shlex.join([hook.PYTHON, str(hook.LIFECYCLE), '--request-json', json.dumps(request)])
        event.update(hook_event_name='PreToolUse', tool_name='Bash', tool_input={'command': command, 'login': False, 'shell': '/bin/sh'})
        self.assertTrue(hook.bootstrap(event))
        for change in [{'login': None}, {'shell': '/tmp/unreviewed-shell'}, {'environment': {'X': 'value'}}]:
            event['tool_input'] = {'command': command, 'login': False, 'shell': '/bin/sh', **change}
            self.assertFalse(hook.bootstrap(event))
        request['arbitrary'] = 'field'
        event['tool_input'] = {'command': shlex.join([hook.PYTHON, str(hook.LIFECYCLE), '--request-json', json.dumps(request)]), 'login': False, 'shell': '/bin/sh'}
        self.assertFalse(hook.bootstrap(event))

    def test_cleaned_archive_reads_are_tombstone_bound(self):
        self.create()
        observations = self.archive()
        self.require_ok(w.execute(self.cleanup_request(observations)))
        event = {'hook_event_name': 'PreToolUse', 'cwd': str(self.root), 'session_id': 'coordinator',
                 'tool_name': 'mcp__codex_apps__linear_get_document', 'tool_input': {'id': 'index'}}
        self.assertTrue(hook.provider_gate(event))
        event['tool_input']['id'] = 'unrelated'
        self.assertFalse(hook.provider_gate(event))

    def test_rebind_retry_does_not_duplicate_and_changed_request_conflicts(self):
        self.create()
        target = self.req('create', issue_id='TEST-2', issue_uuid='second', session_id='other', binding_generation=None)
        target['coordinator'] = w.participant_key(target)
        created = self.require_ok(w.execute(target))
        scope = {**target, 'operation': 'scope', 'request_id': 'target-scope', 'binding_generation': 1,
                 'expected_revision': created['revision'], 'target_participant': self.base['coordinator'], 'packet': []}
        self.require_ok(w.execute(scope))
        request = self.req('rebind', new_issue_id='TEST-2', evidence=self.evidence())
        first = self.require_ok(w.execute(request))
        self.assertEqual(first, w.execute(request))
        altered = copy.deepcopy(request); altered['evidence'][0]['id'] = 'different'
        self.assertEqual(w.execute(altered)['code'], 'REQUEST_CONFLICT')

    def test_archive_prepare_retry_after_seal_is_exactly_once(self):
        self.create()
        self.call('outcome', expected_revision=self.state()['revision'], disposition='cancelled', evidence=self.evidence())
        request = self.req('archive-prepare', expected_revision=self.state()['revision'], seal=True)
        def crash(point):
            if point == 'transaction-complete': raise OSError('after seal')
        with mock.patch.object(w, 'FAILPOINT', crash):
            self.assertFalse(w.execute(request)['ok'])
        first = self.require_ok(w.execute(request))
        self.assertEqual(first, w.execute(request))
        self.assertEqual(self.state()['seq'], 3)

    def test_lifecycle_observation_preserves_pending_operations(self):
        self.create(); self.ready()
        self.call('tool-start', tool_id='pending')
        seq = self.state()['seq']
        hook.handle({'hook_event_name': 'Interrupt', 'cwd': str(self.root), 'session_id': 'coordinator'})
        self.assertEqual(self.state()['seq'], seq + 1)
        self.assertIn('pending', self.state()['participants'][self.base['coordinator']]['pending'])

    def test_attach_reopen_racing_cleanup_never_loses_new_work(self):
        import multiprocessing
        from test_lifecycle import call_process
        self.create()
        observations = self.archive()
        cleanup = self.cleanup_request(observations)
        reopen = self.req('reopen', expected_revision=self.state()['revision'], evidence=self.evidence())
        with multiprocessing.get_context('fork').Pool(2) as pool:
            results = pool.map(call_process, [cleanup, reopen])
        self.assertEqual(sum(r['ok'] for r in results), 1, results)
        state = self.state()
        if state['storage'] == 'cleaned':
            self.assertEqual(state['disposition'], 'cancelled')
        else:
            self.assertEqual(state['disposition'], 'active')
            self.assertTrue((self.root / '.task/TEST-1/roadmap.md').is_file())

    def test_view_swap_blocks_readiness_without_touching_other_directory(self):
        self.create(); self.ready()
        self.call('attach', worktree=str(self.other))
        view = self.other / '.task/TEST-1'
        view.unlink()
        outside = Path(self.temp.name).resolve() / 'outside'
        outside.mkdir(); (outside / 'keep').write_text('sentinel')
        view.symlink_to(outside)
        self.assertFalse(w.execute(self.req('ready', worktree=str(self.other)))['ok'])
        self.assertEqual((outside / 'keep').read_text(), 'sentinel')

    def test_interrupted_automatic_packet_setup_retries_without_overwrite(self):
        self.call('register', repo_id=None, main_worktree=str(self.root), startup={
            'issue_id': 'TEST-1', 'issue_uuid': self.base['issue_uuid'],
            'coordinator': self.base['coordinator'], 'packet': []})
        event = {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.root),
                 'session_id': 'coordinator', 'prompt': 'Task: TEST-1'}
        execute = w.execute
        def fail_scope(request):
            return {'ok': False, 'code': 'BUSY'} if request['operation'] == 'scope' else execute(request)
        with mock.patch.object(w, 'execute', fail_scope):
            with self.assertRaises(w.WorkspaceError):
                hook.handle(event)
        self.assertIsNone(self.state()['participants'][self.base['coordinator']]['packet'])
        hook.handle(event)
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['packet'], [])
        before = self.state()['revision']
        hook.handle(event)
        self.assertEqual(self.state()['revision'], before)

    def test_terminal_reconciliation_requires_explicit_reopen(self):
        self.create()
        self.call('outcome', expected_revision=self.state()['revision'], disposition='cancelled', evidence=self.evidence())
        path = self.root / '.task/TEST-1/roadmap.md'; path.write_text('# changed after terminal')
        inventory = {p: w.sha((self.root / '.task/TEST-1' / p).read_bytes()) for p in self.state()['files']}
        result = w.execute(self.req('reconcile-files', expected_revision=self.state()['revision'], inventory=inventory, evidence=self.evidence()))
        self.assertEqual(result['code'], 'TERMINAL')
        self.assertEqual(path.read_text(), '# changed after terminal')

    def test_narrow_recovery_commands_pass_actual_adapter_gate(self):
        self.create()
        hook.handle({'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.root),
                     'session_id': 'coordinator', 'prompt': 'Task: TEST-1'})
        for operation, fields in [('archive-prepare', {'seal': True}),
                                  ('reconcile-files', {'inventory': {}, 'evidence': self.evidence()}),
                                  ('rebind', {'new_issue_id': 'TEST-2', 'evidence': self.evidence()})]:
            request = self.req(operation, coordinator=None, expected_revision=self.state()['revision'], **fields)
            command = shlex.join([hook.PYTHON, str(hook.LIFECYCLE), '--request-json', json.dumps(request)])
            event = {'hook_event_name': 'PreToolUse', 'cwd': str(self.root), 'session_id': 'coordinator',
                     'tool_name': 'Bash', 'tool_use_id': operation, 'tool_input': {'command': command, 'login': False, 'shell': '/bin/sh'}}
            self.assertEqual(hook.handle(event), {})
            request['unknown_field'] = 'no'
            event['tool_input']['command'] = shlex.join([hook.PYTHON, str(hook.LIFECYCLE), '--request-json', json.dumps(request)])
            self.assertFalse(hook.bootstrap(event))

    def test_multipart_more_than_old_verifier_limit_reconstructs(self):
        self.create()
        with mock.patch.object(w, 'ARCHIVE_CHUNK', 12):
            export = self.seal()
        self.assertGreater(len(export['parts']), 257)
        self.assertLessEqual(len(export['parts']), 513)
        observations = self.observations(export)
        index = self.call('archive-index', observations=observations)
        observations.append({'id': 'index', 'url': 'https://linear.app/test/document/index',
                             'issue': self.base['issue_uuid'], 'updatedAt': '2026-10-03T00:00:00Z',
                             'origin': 'linear_get_document', 'request_id': 'fixture-get', 'content': index['content']})
        self.call('archive-verify', observations=observations)
        stored = self.state()['requests']
        self.assertFalse(any('parts' in item['result'] and 'snapshot' in item['result'] for item in stored.values()))
