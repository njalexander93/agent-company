"""Test direct adapter bootstrap behavior in disposable Git stores.

Provider evidence is synthetic. These tests do not prove live desktop coverage,
provider acceptance, human approval or adversarial runtime isolation.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_company.adapters import codex as hook
from agent_company.adapters import common
from tests.support import Fixture

pytestmark = pytest.mark.integration


class LifecycleTests(Fixture):
    """Exercise local lifecycle, containment and adapter contracts in disposable stores."""

    def test_T14_real_adapter_bootstrap_argument_gate(self) -> None:
        """Check exact bootstrap admission and subprocess denial of shell variants.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Bind the hook session and construct its canonical diagnostic command.
        event = {
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(self.root),
            "session_id": "coordinator",
            "prompt": "Task: TEST-1",
        }
        common.prompt(event, "codex", attempt_attach=True)
        request = self.req(
            "diagnose", repo_id=None, issue_id=None, issue_uuid=None, coordinator=None
        )
        command = common.bootstrap_command(request, "codex")
        # Admit and execute the supported bootstrap command.
        event.update(
            hook_event_name="PreToolUse",
            tool_name="Bash",
            tool_use_id="test",
            tool_input={
                "command": command,
                "login": False,
                "shell": "powershell.exe" if os.name == "nt" else "/bin/sh",
            },
        )
        self.assertEqual(hook.handle(event), {})
        shell = (
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
            if os.name == "nt"
            else ["/bin/sh", "-c", command]
        )
        result = subprocess.run(shell, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        # Reject command chaining, wrappers, redirection and alternate interpreters.
        for bad in [
            command + "; touch sentinel",
            "env " + command,
            command + " > output",
            command + " && true",
            command.replace(hook.PYTHON, "python3", 1),
        ]:
            event["tool_input"]["command"] = bad
            self.assertFalse(hook.bootstrap(event))
        # Feed the rejected command through the actual JSON adapter entry point.
        denied = subprocess.run(
            [hook.PYTHON, str(Path(hook.__file__))],
            cwd=self.root,
            input=json.dumps(event),
            capture_output=True,
            text=True,
        )
        # Verify a successful hook process still emits an explicit tool denial.
        self.assertEqual(denied.returncode, 0)
        self.assertEqual(
            json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecision"], "deny"
        )
