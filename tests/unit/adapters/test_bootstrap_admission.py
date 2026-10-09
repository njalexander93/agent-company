"""Check canonical lifecycle bootstrap authorization against recorded assignment."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_company.adapters import common
from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit

CHECKOUT = Path(Path.cwd().anchor) / "checkout"
OTHER = Path(Path.cwd().anchor) / "other"


def base(operation: str, **extra: object) -> dict[str, Any]:
    """Build one bounded command request for an observed Codex session."""
    return {
        "schema_version": 1,
        "operation": operation,
        "request_id": "request-1",
        "worktree": str(CHECKOUT),
        "host": "codex",
        "session_id": "session",
        **extra,
    }


def test_canonical_bootstrap_allows_exact_diagnostic_and_rejects_extra_unready_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Permit one exact recovery diagnostic without opening assignment state."""
    monkeypatch.setattr(
        common.core,
        "repository",
        lambda _path: (CHECKOUT, CHECKOUT.parent / "common", [CHECKOUT]),
    )
    monkeypatch.setattr(
        common.core.Directory,
        "absolute",
        lambda _path: pytest.fail("opened binding for diagnosis"),
    )
    event = {"cwd": str(CHECKOUT), "session_id": "session"}
    valid = common.bootstrap_command(base("diagnose"), "codex")
    assert common.canonical_bootstrap(event, valid, "codex", ready=False) is True
    extra = common.bootstrap_command(base("diagnose", arbitrary="injection"), "codex")
    assert common.canonical_bootstrap(event, extra, "codex", ready=False) is False
    unknown = common.bootstrap_command(base("delete-all"), "codex")
    assert common.canonical_bootstrap(event, unknown, "codex", ready=False) is False


def test_canonical_bootstrap_requires_issue_to_match_persisted_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept only the selected issue, never a command-supplied alternate issue."""
    root = CHECKOUT
    key = core.participant_key(base("read"))
    opened: list[str] = []

    class Node:
        """Expose a direct three-level root/task/bindings handle chain."""

        def __init__(self, level: int = 0) -> None:
            """Select root, task, or binding lookup."""
            self.level = level

        def __enter__(self) -> Node:
            """Hold the modeled opened directory."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled directory."""

        def child(self, name: str) -> Node:
            """Require direct task then binding traversal."""
            opened.append(name)
            assert name == (".task" if self.level == 0 else ".bindings")
            return Node(self.level + 1)

        def json(self, name: str) -> dict[str, str]:
            """Read only the persisted assignment for the actual session."""
            assert (self.level, name) == (2, key + ".assignment.json")
            return {"issue_id": "AGENT-30"}

    monkeypatch.setattr(common.core, "repository", lambda _path: (root, None, [root]))
    monkeypatch.setattr(common.core.Directory, "absolute", lambda _path: Node())
    event = {"cwd": str(CHECKOUT), "session_id": "session"}
    matching = common.bootstrap_command(base("read", issue_id="AGENT-30"), "codex")
    foreign = common.bootstrap_command(base("read", issue_id="AGENT-31"), "codex")
    assert common.canonical_bootstrap(event, matching, "codex", ready=False) is True
    assert common.canonical_bootstrap(event, foreign, "codex", ready=False) is False
    assert opened == [".task", ".bindings", ".task", ".bindings"]


def test_canonical_bootstrap_rejects_foreign_worktree_before_binding_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject commands redirected to another Git worktree before reading assignment."""
    monkeypatch.setattr(common.core, "repository", lambda path: (Path(path), None, []))
    monkeypatch.setattr(
        common.core.Directory,
        "absolute",
        lambda _path: pytest.fail("opened assignment for foreign worktree"),
    )
    event = {"cwd": str(CHECKOUT), "session_id": "session"}
    request = base("read", issue_id="AGENT-30", worktree=str(OTHER))
    command = common.bootstrap_command(request, "codex")
    assert common.canonical_bootstrap(event, command, "codex", ready=False) is False


def test_windows_bootstrap_command_roundtrips_only_literal_powershell_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Encode the bounded request and reject altered PowerShell command structure."""
    monkeypatch.setattr(common, "os", SimpleNamespace(name="nt"))
    request = base("diagnose")
    command = common.bootstrap_command(request, "codex")
    assert command.startswith("& '")
    assert common.bootstrap_request(command, "codex", common.PYTHON, common.LIFECYCLE) == request
    for changed in (command + " ; Write-Host bad", command.replace("& ", "", 1)):
        with pytest.raises(ValueError, match="Noncanonical PowerShell command"):
            common.bootstrap_request(changed, "codex", common.PYTHON, common.LIFECYCLE)


def test_windows_bootstrap_rejects_unsafe_literal_argv_and_wrong_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows bootstrap accepts only the exact encoded entry and safe literal argv."""
    monkeypatch.setattr(common, "os", SimpleNamespace(name="nt"))
    request = base("diagnose")
    command = common.bootstrap_command(request, "codex")
    with pytest.raises(ValueError, match="Unexpected Windows bootstrap entry"):
        common.bootstrap_request(
            command.replace("--request-base64", "--other"),
            "codex",
            common.PYTHON,
            common.LIFECYCLE,
        )
    with pytest.raises(ValueError, match="Unsupported PowerShell quoting"):
        common.bootstrap_request(
            command.replace(common.PYTHON, common.PYTHON + '"'),
            "codex",
            common.PYTHON,
            common.LIFECYCLE,
        )
    monkeypatch.setattr(common, "PYTHON", 'unsafe"python')
    with pytest.raises(ValueError, match="Unsupported PowerShell argument"):
        common.bootstrap_command(request, "codex")
