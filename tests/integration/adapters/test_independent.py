"""Independent contract probes. All payloads live in disposable Git repositories.

Provider observations are fixtures: these tests make no provider/desktop acceptance claim.
"""

import json
import shlex

import pytest

from agent_company.adapters import codex as hook
from agent_company.lifecycle import task_workspace as w
from tests.support import Fixture

pytestmark = pytest.mark.integration


class IndependentContractTests(Fixture):
    """Probe lifecycle boundaries independently using disposable stores and fake provider reads."""

    def test_T08_async_handle_retains_participant_after_session_end(self) -> None:
        """Keep an asynchronous operation pending across session lifecycle events.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Admit a tool and record its still-running asynchronous handle.
        self.create()
        self.ready()
        self.call("tool-start", tool_id="async-tool")
        self.call(
            "tool-complete", tool_id="async-tool", async_handle="remote-process-17", completed=False
        )
        # Deliver each lifecycle observation without claiming tool completion.
        for event in ("Stop", "Interrupt", "SessionEnd"):
            hook.handle(
                {"hook_event_name": event, "cwd": str(self.root), "session_id": "coordinator"}
            )
        # Require pending-work retention of both participant state and task bytes.
        self.assertEqual(
            w.execute(self.req("detach", evidence=self.evidence()))["code"], "PENDING_OPERATION"
        )
        self.assertIn(
            "async-tool", self.state()["participants"][self.base["coordinator"]]["pending"]
        )
        self.assertTrue((self.root / ".task/TEST-1").is_dir())

    def test_T14_bootstrap_session_issue_and_shell_injection_gate(self) -> None:
        """Reject bootstrap identity and shell variants without granting host permission.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Bind the explicit issue before constructing malformed bootstrap inputs.
        hook.handle(
            {
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(self.root),
                "session_id": "coordinator",
                "prompt": "Task: TEST-1",
            }
        )
        event = {"cwd": str(self.root), "session_id": "coordinator", "tool_name": "exec_command"}
        request = self.req("create")
        # Vary session, issue and operation in the attempted bootstrap request.
        for changes in ({"session_id": "foreign"}, {"issue_id": "TEST-2"}, {"operation": "update"}):
            changed = {**request, **changes}
            event["tool_input"] = {
                "cmd": shlex.join(
                    [hook.PYTHON, str(hook.LIFECYCLE), "--request-json", json.dumps(changed)]
                ),
                "login": False,
            }
            self.assertFalse(hook.bootstrap(event))
        # Construct shell variants from the same request for rejection checks.
        command = shlex.join(
            [hook.PYTHON, str(hook.LIFECYCLE), "--request-json", json.dumps(request)]
        )
        # Reject wrappers, pipelines, backgrounding and environment prefixes.
        for bad in (
            command + "\ntrue",
            "(" + command + ")",
            command + " | cat",
            command + " &",
            "X=1 " + command,
        ):
            event["tool_input"] = {"cmd": bad, "login": False}
            self.assertFalse(hook.bootstrap(event))
        # Verify permission callbacks never grant host permissions.
        self.assertEqual(hook.handle({"hook_event_name": "PermissionRequest"}), {})
