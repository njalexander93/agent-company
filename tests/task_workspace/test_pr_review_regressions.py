"""Reproduce PR findings through real adapter/core calls in disposable repositories."""
import json
import os
from unittest import mock

from test_lifecycle import Fixture, w, hook


class PRReviewRegressions(Fixture):
    """Exercise host recovery, async settlement and metadata-safe lifecycle paths."""

    def event(self, name, **fields):
        return {'hook_event_name': name, 'cwd': str(self.root),
                'session_id': 'coordinator', **fields}

    def start_tool(self, identifier='original'):
        result = hook.handle(self.event('PreToolUse', tool_name='Bash',
                                       tool_use_id=identifier, tool_input={'command': 'true'}))
        self.assertEqual(result, {})
        return identifier

    def post_tool(self, response, identifier='original', tool_name='Bash', tool_input=None):
        return hook.handle(self.event('PostToolUse', tool_name=tool_name,
                                      tool_use_id=identifier, tool_input=tool_input or {},
                                      tool_response=response))

    def test_B1_lifecycle_allows_recovery_without_granting_readiness(self):
        self.create(); self.ready()
        self.call('scope', expected_revision=self.state()['revision'],
                  target_participant=self.base['coordinator'], packet=[])
        for name in ('SessionStart', 'PreCompact', 'PostCompact'):
            with self.subTest(name=name):
                result = hook.handle(self.event(name))
                self.assertNotEqual(result.get('continue'), False, result)
                self.assertIn('NOT_READY', json.dumps(result))
        denied = hook.handle(self.event('PreToolUse', tool_name='Bash',
                                       tool_use_id='unready', tool_input={'command': 'true'}))
        self.assertEqual(denied['hookSpecificOutput']['permissionDecision'], 'deny')
        self.call('acknowledge', packet_digest=w.sha(w.canonical([])))
        self.call('detach', evidence=self.evidence())
        self.assertNotEqual(hook.handle(self.event('SessionStart')).get('continue'), False)
        resumed = self.call('resume')
        self.base['binding_generation'] = resumed['binding_generation']
        self.call('acknowledge', packet_digest=w.sha(w.canonical([])))
        self.start_tool()

    def test_B1_preserves_request_error_code(self):
        with mock.patch.object(hook, 'request_for', side_effect=w.WorkspaceError('REPOSITORY_MISMATCH')):
            result = hook.handle(self.event('SessionStart'))
        self.assertIn('REPOSITORY_MISMATCH', json.dumps(result))
        self.assertNotIn('BINDING_MISSING', json.dumps(result))

    def test_B2_final_handle_response_clears_pending(self):
        self.create(); self.ready(); self.start_tool()
        self.post_tool({'session_id': 123})
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending']['original']['handle'], '123')
        self.post_tool({'session_id': 123, 'exit_code': 0})
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})

    def test_B2_poll_completion_resolves_original_handle(self):
        self.create(); self.ready(); self.start_tool()
        self.post_tool({'session_id': 123})
        self.post_tool({'exit_code': 0}, identifier='poll', tool_name='write_stdin',
                       tool_input={'session_id': 123})
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})

    def test_B3_metadata_survives_until_narrow_verified_cleanup(self):
        self.create(); self.ready()
        payload = self.root / '.task/TEST-1'
        for directory in (payload, payload / 'context'):
            for name in ('.DS_Store', '._roadmap.md'):
                (directory / name).write_bytes(b'fixture metadata')
        self.call('ready')
        state = self.state()
        self.call('update', path='roadmap.md', content='# preserved work\n',
                  old_digest=state['files']['roadmap.md'], expected_revision=state['revision'],
                  provenance={'sources': self.evidence(), 'status': 'draft', 'applicability': 'test'})
        observations = self.archive()
        snapshot, files, receipt = w.verify_provider(self.state(), observations)
        self.assertFalse(any('.DS_Store' in name or '/._' in name or name.startswith('._') for name in files))
        self.require_ok(w.execute(self.cleanup_request(observations)))
        self.assertFalse(payload.exists())
        self.call('restore', observations=observations)
        self.assertEqual((payload / 'roadmap.md').read_text(), '# preserved work\n')

    def test_B1_real_repository_mismatch_is_not_unbound(self):
        registration = self.root / '.task/.repository.json'
        data = json.loads(registration.read_text())
        data['common_identity'] = [-1, -1]
        registration.write_text(json.dumps(data))
        for name in ('SessionStart', 'PreCompact', 'PostCompact'):
            result = hook.handle(self.event(name))
            self.assertIn('REPOSITORY_MISMATCH', json.dumps(result))
            self.assertNotIn('BINDING_MISSING', json.dumps(result))

    def test_B2_ambiguous_metadata_and_conflicting_handles_retain_pending(self):
        self.create(); self.ready(); self.start_tool()
        for response in ({}, {'session_id': None, 'exit_code': None, 'isError': None},
                         {'exit_code': False}, {'exit_code': '0'}, {'isError': 'false'}):
            self.post_tool(response)
            self.assertIn('original', self.state()['participants'][self.base['coordinator']]['pending'])
        self.post_tool({'session_id': 123, 'isError': False})
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending']['original']['handle'], '123')
        before = self.state()
        rejected = self.post_tool({'session_id': 456, 'exit_code': 0})
        self.assertIn('REQUEST_CONFLICT', json.dumps(rejected))
        self.assertEqual(self.state(), before)
        self.post_tool({'session_id': 456, 'exit_code': 0}, identifier='poll-conflict',
                       tool_name='write_stdin', tool_input={'session_id': 123})
        self.assertEqual(self.state(), before)
        self.post_tool({'session_id': 123, 'exit_code': 0})
        completed = self.state()
        self.post_tool({'session_id': 123, 'exit_code': 0})
        self.assertEqual(self.state(), completed)

    def test_B2_polling_ambiguous_handle_does_not_choose_an_operation(self):
        self.create(); self.ready(); self.start_tool('first'); self.start_tool('second')
        self.post_tool({'session_id': 123}, identifier='first')
        self.post_tool({'session_id': 123}, identifier='second')
        before = self.state()
        result = self.post_tool({'exit_code': 0}, identifier='poll', tool_name='write_stdin',
                                tool_input={'session_id': 123})
        self.assertIn('UNKNOWN_OPERATION', json.dumps(result))
        self.assertEqual(self.state(), before)

    def test_B2_typed_mcp_completion_and_core_handle_validation(self):
        self.create(); self.ready(); self.start_tool()
        self.post_tool({'isError': None}, tool_name='mcp__fixture__read')
        self.assertIn('original', self.state()['participants'][self.base['coordinator']]['pending'])
        self.post_tool({'isError': False}, tool_name='mcp__fixture__read')
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})
        self.start_tool('next')
        self.call('tool-complete', tool_id='next', async_handle='123')
        before = self.state()
        denied = w.execute(self.req('tool-complete', tool_id='next', async_handle='456', completed=True))
        self.assertEqual(denied['code'], 'REQUEST_CONFLICT')
        self.assertEqual(self.state(), before)
        self.call('tool-complete', tool_id='next', async_handle='123', completed=True)
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})

    def test_B3_unsafe_metadata_and_other_dotfiles_are_not_ignored(self):
        self.create(); self.ready()
        sentinel = self.root / 'outside-sentinel'
        sentinel.write_bytes(b'outside unchanged')
        payload = self.root / '.task/TEST-1'
        for directory in (payload, payload / 'context'):
            for name in ('.DS_Store', '._roadmap.md'):
                path = directory / name
                path.symlink_to(sentinel)
                self.assertFalse(w.execute(self.req('ready'))['ok'])
                path.unlink()
                os.link(sentinel, path)
                self.assertFalse(w.execute(self.req('ready'))['ok'])
                path.unlink()
                path.mkdir()
                self.assertFalse(w.execute(self.req('ready'))['ok'])
                path.rmdir()
            foreign = directory / '.foreign'
            foreign.write_text('foreign data')
            self.assertFalse(w.execute(self.req('ready'))['ok'])
            self.assertEqual(foreign.read_text(), 'foreign data')
            foreign.unlink()
        self.assertEqual(sentinel.read_bytes(), b'outside unchanged')

    def test_B3_quarantine_metadata_substitution_preserves_outside_and_payload(self):
        self.create(); self.ready()
        observations = self.archive()
        sentinel = self.root / 'outside-sentinel'
        sentinel.write_bytes(b'outside unchanged')
        control = self.root / '.task/.control/issues/TEST-1'
        request = self.cleanup_request(observations)
        def substitute(point):
            if point == 'quarantine':
                intent = json.loads((control / 'cleanup.json').read_text())
                (control / intent['quarantine'] / '.DS_Store').symlink_to(sentinel)
        with mock.patch.object(w, 'FAILPOINT', substitute):
            result = w.execute(request)
        self.assertFalse(result['ok'])
        intent = json.loads((control / 'cleanup.json').read_text())
        quarantine = control / intent['quarantine']
        self.assertTrue((quarantine / 'roadmap.md').exists())
        self.assertEqual(sentinel.read_bytes(), b'outside unchanged')
        (quarantine / '.DS_Store').unlink()
        self.require_ok(w.execute(request))
