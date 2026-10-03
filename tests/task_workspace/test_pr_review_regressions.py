"""Reproduce PR findings through real adapter/core calls in disposable repositories."""
import json
import os
from unittest import mock

from test_lifecycle import Fixture, w, hook


class PRReviewRegressions(Fixture):
    """Group real adapter/core regressions without touching live workspaces.
    """

    def event(self, name, **fields):
        """Build a synthetic hook envelope for this disposable session.

        Args:
            name: Direct entry name relative to the opened directory.
            fields: Additional synthetic hook fields for the disposable regression fixture.

        Returns:
            The complete synthetic event object.
        """
        return {'hook_event_name': name, 'cwd': str(self.root),
                'session_id': 'coordinator', **fields}

    def start_tool(self, identifier='original'):
        """Admit a harmless synthetic tool through the actual readiness adapter.

        Args:
            identifier: Validated issue ID or explicit tool-call identity.

        Returns:
            The admitted tool-call identifier.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        result = hook.handle(self.event('PreToolUse', tool_name='Bash',
                                       tool_use_id=identifier, tool_input={'command': 'true'}))
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(result, {})
        return identifier

    def post_tool(self, response, identifier='original', tool_name='Bash', tool_input=None):
        """Deliver a synthetic host response through the actual completion adapter.

        Args:
            response: Synthetic host response passed through the actual adapter.
            identifier: Validated issue ID or explicit tool-call identity.
            tool_name: Canonical host tool name used to select completion semantics.
            tool_input: Synthetic arguments used for polling-handle association.

        Returns:
            The hook response after attempted pending-work settlement.

        Raises:
            w.WorkspaceError: If the actual adapter cannot resolve the fixture binding.
        """
        return hook.handle(self.event('PostToolUse', tool_name=tool_name,
                                      tool_use_id=identifier, tool_input=tool_input or {},
                                      tool_response=response))

    def test_B1_lifecycle_allows_recovery_without_granting_readiness(self):
        """Prove lifecycle recovery remains reachable while ordinary unready tools stay denied.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.create(); self.ready()
        self.call('scope', expected_revision=self.state()['revision'],
                  target_participant=self.base['coordinator'], packet=[])
        # Process each name under the same validation boundary.
        for name in ('SessionStart', 'PreCompact', 'PostCompact'):
            # Report each hook or metadata variant as its own regression case.
            with self.subTest(name=name):
                # Arrange the disposable fixture and exercise the real lifecycle boundary.
                result = hook.handle(self.event(name))
                # Assert the expected safety result and preserved fixture state.
                self.assertNotEqual(result.get('continue'), False, result)
                self.assertIn('NOT_READY', json.dumps(result))
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        denied = hook.handle(self.event('PreToolUse', tool_name='Bash',
                                       tool_use_id='unready', tool_input={'command': 'true'}))
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(denied['hookSpecificOutput']['permissionDecision'], 'deny')
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.call('acknowledge', packet_digest=w.sha(w.canonical([])))
        self.call('detach', evidence=self.evidence())
        # Assert the expected safety result and preserved fixture state.
        self.assertNotEqual(hook.handle(self.event('SessionStart')).get('continue'), False)
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        resumed = self.call('resume')
        self.base['binding_generation'] = resumed['binding_generation']
        self.call('acknowledge', packet_digest=w.sha(w.canonical([])))
        self.start_tool()

    def test_B1_preserves_request_error_code(self):
        """Prove an exact request-construction error survives lifecycle reporting.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Confine the injected failure to this disposable regression action.
        with mock.patch.object(hook, 'request_for', side_effect=w.WorkspaceError('REPOSITORY_MISMATCH')):
            result = hook.handle(self.event('SessionStart'))
        # Assert the expected safety result and preserved fixture state.
        self.assertIn('REPOSITORY_MISMATCH', json.dumps(result))
        self.assertNotIn('BINDING_MISSING', json.dumps(result))

    def test_B2_final_handle_response_clears_pending(self):
        """Prove explicit final completion settles an operation even when its handle remains.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.create(); self.ready(); self.start_tool()
        self.post_tool({'session_id': 123})
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending']['original']['handle'], '123')
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.post_tool({'session_id': 123, 'exit_code': 0})
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})

    def test_B2_poll_completion_resolves_original_handle(self):
        """Prove a distinct polling call settles only its original handle-bound operation.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.create(); self.ready(); self.start_tool()
        self.post_tool({'session_id': 123})
        self.post_tool({'exit_code': 0}, identifier='poll', tool_name='write_stdin',
                       tool_input={'session_id': 123})
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})

    def test_B3_metadata_survives_until_narrow_verified_cleanup(self):
        """Prove safe Finder metadata permits updates, verified cleanup and complete restore.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.create(); self.ready()
        payload = self.root / '.task/TEST-1'
        # Process each directory under the same validation boundary.
        for directory in (payload, payload / 'context'):
            # Process each name under the same validation boundary.
            for name in ('.DS_Store', '._roadmap.md'):
                (directory / name).write_bytes(b'fixture metadata')
        # Stage the verified lifecycle changes in the issue state.
        self.call('ready')
        state = self.state()
        self.call('update', path='roadmap.md', content='# preserved work\n',
                  old_digest=state['files']['roadmap.md'], expected_revision=state['revision'],
                  provenance={'sources': self.evidence(), 'status': 'draft', 'applicability': 'test'})
        observations = self.archive()
        snapshot, files, receipt = w.verify_provider(self.state(), observations)
        # Assert the expected safety result and preserved fixture state.
        self.assertFalse(any('.DS_Store' in name or '/._' in name or name.startswith('._') for name in files))
        self.require_ok(w.execute(self.cleanup_request(observations)))
        self.assertFalse(payload.exists())
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.call('restore', observations=observations)
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual((payload / 'roadmap.md').read_text(), '# preserved work\n')

    def test_B1_real_repository_mismatch_is_not_unbound(self):
        """Prove corrupted repository identity is not mislabeled as an absent binding.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        registration = self.root / '.task/.repository.json'
        # Read repository registration through validated directory handles.
        data = json.loads(registration.read_text())
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        data['common_identity'] = [-1, -1]
        registration.write_text(json.dumps(data))
        # Process each name under the same validation boundary.
        for name in ('SessionStart', 'PreCompact', 'PostCompact'):
            # Arrange the disposable fixture and exercise the real lifecycle boundary.
            result = hook.handle(self.event(name))
            # Assert the expected safety result and preserved fixture state.
            self.assertIn('REPOSITORY_MISMATCH', json.dumps(result))
            self.assertNotIn('BINDING_MISSING', json.dumps(result))

    def test_B2_ambiguous_metadata_and_conflicting_handles_retain_pending(self):
        """Prove malformed or conflicting observations retain work and exact retries are idempotent.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.create(); self.ready(); self.start_tool()
        # Process each response under the same validation boundary.
        for response in ({}, {'session_id': None, 'exit_code': None, 'isError': None},
                         {'exit_code': False}, {'exit_code': '0'}, {'isError': 'false'}):
            # Normalize response metadata without inferring completion from missing values.
            self.post_tool(response)
            # Assert the expected safety result and preserved fixture state.
            self.assertIn('original', self.state()['participants'][self.base['coordinator']]['pending'])
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.post_tool({'session_id': 123, 'isError': False})
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending']['original']['handle'], '123')
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        before = self.state()
        rejected = self.post_tool({'session_id': 456, 'exit_code': 0})
        # Assert the expected safety result and preserved fixture state.
        self.assertIn('REQUEST_CONFLICT', json.dumps(rejected))
        self.assertEqual(self.state(), before)
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.post_tool({'session_id': 456, 'exit_code': 0}, identifier='poll-conflict',
                       tool_name='write_stdin', tool_input={'session_id': 123})
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(self.state(), before)
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.post_tool({'session_id': 123, 'exit_code': 0})
        completed = self.state()
        self.post_tool({'session_id': 123, 'exit_code': 0})
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(self.state(), completed)

    def test_B2_polling_ambiguous_handle_does_not_choose_an_operation(self):
        """Prove a shared handle cannot ambiguously settle one of two pending operations.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.create(); self.ready(); self.start_tool('first'); self.start_tool('second')
        self.post_tool({'session_id': 123}, identifier='first')
        self.post_tool({'session_id': 123}, identifier='second')
        before = self.state()
        result = self.post_tool({'exit_code': 0}, identifier='poll', tool_name='write_stdin',
                                tool_input={'session_id': 123})
        # Assert the expected safety result and preserved fixture state.
        self.assertIn('UNKNOWN_OPERATION', json.dumps(result))
        self.assertEqual(self.state(), before)

    def test_B2_typed_mcp_completion_and_core_handle_validation(self):
        """Prove typed MCP completion and core handle checks preserve settlement identity.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.create(); self.ready(); self.start_tool()
        self.post_tool({'isError': None}, tool_name='mcp__fixture__read')
        # Assert the expected safety result and preserved fixture state.
        self.assertIn('original', self.state()['participants'][self.base['coordinator']]['pending'])
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.post_tool({'isError': False}, tool_name='mcp__fixture__read')
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.start_tool('next')
        self.call('tool-complete', tool_id='next', async_handle='123')
        before = self.state()
        denied = w.execute(self.req('tool-complete', tool_id='next', async_handle='456', completed=True))
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(denied['code'], 'REQUEST_CONFLICT')
        self.assertEqual(self.state(), before)
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.call('tool-complete', tool_id='next', async_handle='123', completed=True)
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})

    def test_B3_unsafe_metadata_and_other_dotfiles_are_not_ignored(self):
        """Prove metadata exemptions reject links, directories and unrelated hidden files.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        self.create(); self.ready()
        sentinel = self.root / 'outside-sentinel'
        sentinel.write_bytes(b'outside unchanged')
        payload = self.root / '.task/TEST-1'
        # Process each directory under the same validation boundary.
        for directory in (payload, payload / 'context'):
            # Process each name under the same validation boundary.
            for name in ('.DS_Store', '._roadmap.md'):
                # Arrange the disposable fixture and exercise the real lifecycle boundary.
                path = directory / name
                path.symlink_to(sentinel)
                # Assert the expected safety result and preserved fixture state.
                self.assertFalse(w.execute(self.req('ready'))['ok'])
                # Release the scoped resource or remove the already validated direct entry.
                path.unlink()
                # Arrange the disposable fixture and exercise the real lifecycle boundary.
                os.link(sentinel, path)
                # Assert the expected safety result and preserved fixture state.
                self.assertFalse(w.execute(self.req('ready'))['ok'])
                # Release the scoped resource or remove the already validated direct entry.
                path.unlink()
                # Arrange the disposable fixture and exercise the real lifecycle boundary.
                path.mkdir()
                # Assert the expected safety result and preserved fixture state.
                self.assertFalse(w.execute(self.req('ready'))['ok'])
                # Release the scoped resource or remove the already validated direct entry.
                path.rmdir()
            # Arrange the disposable fixture and exercise the real lifecycle boundary.
            foreign = directory / '.foreign'
            foreign.write_text('foreign data')
            # Assert the expected safety result and preserved fixture state.
            self.assertFalse(w.execute(self.req('ready'))['ok'])
            self.assertEqual(foreign.read_text(), 'foreign data')
            # Release the scoped resource or remove the already validated direct entry.
            foreign.unlink()
        # Assert the expected safety result and preserved fixture state.
        self.assertEqual(sentinel.read_bytes(), b'outside unchanged')

    def test_B3_quarantine_metadata_substitution_preserves_outside_and_payload(self):
        """Prove substituted metadata stops cleanup before verified payload deletion.

        Raises:
            AssertionError: If the regression violates its expected safety or recovery result.
        """
        # Build the request from explicit caller or observed session identities.
        self.create(); self.ready()
        observations = self.archive()
        sentinel = self.root / 'outside-sentinel'
        sentinel.write_bytes(b'outside unchanged')
        control = self.root / '.task/.control/issues/TEST-1'
        request = self.cleanup_request(observations)
        def substitute(point):
            """Inject a metadata symlink only after the disposable issue enters quarantine.

            Args:
                point: Named internal transaction boundary for a test injection.
            """
            # Handle the case point == 'quarantine'.
            if point == 'quarantine':
                # Read substitute inputs through the scoped file interface.
                intent = json.loads((control / 'cleanup.json').read_text())
                # Arrange the disposable fixture and exercise the real lifecycle boundary.
                (control / intent['quarantine'] / '.DS_Store').symlink_to(sentinel)
        # Confine the injected failure to this disposable regression action.
        with mock.patch.object(w, 'FAILPOINT', substitute):
            result = w.execute(request)
        # Assert the expected safety result and preserved fixture state.
        self.assertFalse(result['ok'])
        # Read test_B3_quarantine_metadata_substitution_preserves_outside_and_payload inputs through the scoped file interface.
        intent = json.loads((control / 'cleanup.json').read_text())
        # Arrange the disposable fixture and exercise the real lifecycle boundary.
        quarantine = control / intent['quarantine']
        # Assert the expected safety result and preserved fixture state.
        self.assertTrue((quarantine / 'roadmap.md').exists())
        self.assertEqual(sentinel.read_bytes(), b'outside unchanged')
        # Release the scoped resource or remove the already validated direct entry.
        (quarantine / '.DS_Store').unlink()
        # Assert the expected safety result and preserved fixture state.
        self.require_ok(w.execute(request))

    def test_B2_full_poll_lifecycle_retires_transport_and_original(self):
        """Settle every observed polling call without prematurely settling its process.

        Raises:
            AssertionError: If polling strands work or retires the process too early.
        """
        # Admit the original process and observe its still-running handle.
        self.create(); self.ready(); self.start_tool()
        self.post_tool({'session_id': 123})
        # Exercise nonfinal and final polls through both actual hook boundaries.
        for identifier, response in [('poll-one', {'session_id': 123}),
                                     ('poll-two', {'session_id': 123, 'exit_code': 0})]:
            admitted = hook.handle(self.event('PreToolUse', tool_name='write_stdin',
                                             tool_use_id=identifier, tool_input={'session_id': 123}))
            self.assertEqual(admitted, {})
            self.post_tool(response, identifier=identifier, tool_name='write_stdin',
                           tool_input={'session_id': 123})
            pending = self.state()['participants'][self.base['coordinator']]['pending']
            self.assertNotIn(identifier, pending)
            # A nonfinal poll is finished transport, while the original process remains live.
            if 'exit_code' not in response:
                self.assertIn('original', pending)
        # Terminal disposition and retirement must now be reachable without manual edits.
        self.assertEqual(pending, {})
        self.call('outcome', expected_revision=self.state()['revision'], disposition='cancelled', evidence=self.evidence())
        self.call('detach', evidence=self.evidence())

    def test_B2_malformed_explicit_handles_cannot_retire_work(self):
        """Reject malformed handle metadata instead of converting it to an absent handle.

        Raises:
            AssertionError: If invalid response or poll handles permit settlement.
        """
        # Establish a known running process whose retirement must remain blocked.
        self.create(); self.ready(); self.start_tool()
        self.post_tool({'session_id': 123})
        before = self.state()
        # Exercise invalid JSON types and empty tokens at both correlation boundaries.
        for malformed in ([], {}, True, False, '', ' ', 1.5):
            with self.subTest(handle=malformed):
                self.post_tool({'session_id': malformed, 'exit_code': 0})
                self.assertEqual(self.state(), before)
                self.post_tool({'exit_code': 0}, identifier='bad-poll', tool_name='write_stdin',
                               tool_input={'session_id': malformed})
                self.assertEqual(self.state(), before)
                self.post_tool({'session_id': malformed, 'exit_code': 0}, identifier='bad-response',
                               tool_name='write_stdin', tool_input={'session_id': 123})
                self.assertEqual(self.state(), before)
        # Missing metadata remains valid when the original call ID supplies correlation.
        self.assertEqual(w.execute(self.req('detach', evidence=self.evidence()))['code'], 'PENDING_OPERATION')
        self.post_tool({'exit_code': 0})
        self.call('detach', evidence=self.evidence())

    def test_B2_concurrent_polls_retire_after_one_settles_parent(self):
        """Retire independently admitted transports after a sibling settles their parent.

        Raises:
            AssertionError: If concurrent polls strand transport state or duplicate events.
        """
        # Establish one asynchronous parent and two distinctly observed polling calls.
        self.create(); self.ready(); self.start_tool()
        self.post_tool({'session_id': 123})
        for identifier in ('poll-a', 'poll-b'):
            self.assertEqual(hook.handle(self.event('PreToolUse', tool_name='write_stdin',
                                                   tool_use_id=identifier, tool_input={'session_id': 123})), {})
        # The first final response settles only its own transport and the parent.
        self.post_tool({'exit_code': 0}, identifier='poll-a', tool_name='write_stdin',
                       tool_input={'session_id': 123})
        pending = self.state()['participants'][self.base['coordinator']]['pending']
        self.assertEqual(set(pending), {'poll-b'})
        # A later response can retire its recorded transport without recreating the parent.
        self.post_tool({'session_id': 123}, identifier='poll-b', tool_name='write_stdin',
                       tool_input={'session_id': 123})
        settled = self.state()
        self.assertEqual(settled['participants'][self.base['coordinator']]['pending'], {})
        self.post_tool({'session_id': 123}, identifier='poll-b', tool_name='write_stdin',
                       tool_input={'session_id': 123})
        self.assertEqual(self.state(), settled)

    def test_B2_poll_completion_preserves_capacity_and_rejects_bad_admission(self):
        """Reserve settlement capacity for polling while rejecting malformed admission.

        Raises:
            AssertionError: If a denied poll changes state or admitted work cannot settle.
        """
        # Reject invalid handles before a polling tool can create pending state.
        self.create(); self.ready(); self.start_tool()
        self.post_tool({'session_id': 123})
        before = self.state()
        for malformed in ([], True, '', None):
            result = hook.handle(self.event('PreToolUse', tool_name='write_stdin',
                                            tool_use_id='invalid-poll', tool_input={'session_id': malformed}))
            self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')
            self.assertEqual(self.state(), before)
        # Admit a valid polling transport with enough space for both settlement records.
        active = self.root / '.task/TEST-1/events.jsonl'
        with mock.patch.object(w, 'MAX_EVENTS', active.stat().st_size + 4 * 8192 + 2000):
            self.assertEqual(hook.handle(self.event('PreToolUse', tool_name='write_stdin',
                                                   tool_use_id='bounded-poll', tool_input={'session_id': 123})), {})
            # Exhaust ordinary capacity without consuming the reserved completion space.
            for _ in range(100):
                result = w.execute(self.req('event', event_type='check', event={'code': 'OK'}))
                if not result['ok']:
                    self.assertEqual(result['code'], 'ARCHIVE_PENDING')
                    break
            else:
                self.fail('The patched event capacity was not exhausted')
            # One explicit final response settles parent and transport, leaving an archivable issue.
            self.post_tool({'session_id': 123, 'exit_code': 0}, identifier='bounded-poll',
                           tool_name='write_stdin', tool_input={'session_id': 123})
            self.assertEqual(self.state()['participants'][self.base['coordinator']]['pending'], {})
            self.call('archive-prepare', expected_revision=self.state()['revision'])
