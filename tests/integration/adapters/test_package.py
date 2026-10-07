"""Exercise real package entry points from outside the editable source checkout."""

import json
import subprocess

import pytest

from agent_company.adapters import codex as hook
from agent_company.lifecycle import task_workspace as w
from tests.support import Fixture

pytestmark = pytest.mark.integration


class PackageEntryPointTests(Fixture):
    """Verify local installed entry points without broad platform or wheel claims."""

    def test_core_and_adapter_module_entry_points_outside_repository(self) -> None:
        """Run both package entry points in isolated subprocesses outside the checkout.

        Raises:
            AssertionError: Either installed entry point fails or emits an invalid response.
        """
        # Create an issue and invoke the lifecycle module against its real fixture state.
        self.create()
        request = self.req("diagnose")
        core = subprocess.run(
            [
                hook.PYTHON,
                "-I",
                "-m",
                "agent_company.lifecycle.task_workspace",
                "--request-json",
                json.dumps(request),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(core.returncode, 0, core.stderr + core.stdout)
        self.require_ok(json.loads(core.stdout))
        # Invoke the adapter's JSON wire entry point with an ordinary permission request.
        adapter = subprocess.run(
            [hook.PYTHON, "-I", "-m", "agent_company.adapters.codex"],
            cwd=self.root,
            input=json.dumps({"hook_event_name": "PermissionRequest"}),
            capture_output=True,
            text=True,
        )
        self.assertEqual(adapter.returncode, 0, adapter.stderr + adapter.stdout)
        self.assertEqual(json.loads(adapter.stdout), {})

    def test_adapter_failpoint_uses_canonical_lifecycle_module(self) -> None:
        """Observe adapter creation at the canonical lifecycle failure-injection seam.

        Raises:
            AssertionError: The adapter bypasses the shared module or injected persistence seam.
        """
        # Register automatic creation so the adapter must execute the real core transaction.
        from unittest import mock

        self.call(
            "register",
            repo_id=None,
            main_worktree=str(self.root),
            startup={
                "issue_id": "TEST-1",
                "issue_uuid": self.base["issue_uuid"],
                "coordinator": self.base["coordinator"],
                "packet": [],
            },
        )
        self.assertIs(hook.core, w)
        # Patch the canonical module and record persistence seams reached by the adapter.
        with mock.patch.object(w, "FAILPOINT") as failpoint:
            hook.handle(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": str(self.root),
                    "session_id": "coordinator",
                    "prompt": "Task: TEST-1",
                }
            )
        # Verify the adapter traversed the patched transaction and created the roadmap.
        failpoint.assert_any_call("transaction-complete")
        self.assertTrue((self.root / ".task/TEST-1/roadmap.md").is_file())
