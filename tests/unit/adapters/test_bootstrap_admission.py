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
    """Build one bounded command request for an observed Codex session.

    Args:
        operation: Lifecycle operation encoded into the shell command.
        extra: Request fields used to test exact admission.

    Returns:
        A lifecycle request bound to the disposable session.
    """
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
    """Permit one exact recovery diagnostic without opening assignment state.

    Args:
        monkeypatch: Replaces repository and binding access boundaries.
    """
    # Diagnose a registered checkout without opening an assignment directory.
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
    # Extra fields and unknown operations cannot use the narrow unready exception.
    extra = common.bootstrap_command(base("diagnose", arbitrary="injection"), "codex")
    assert common.canonical_bootstrap(event, extra, "codex", ready=False) is False
    unknown = common.bootstrap_command(base("delete-all"), "codex")
    assert common.canonical_bootstrap(event, unknown, "codex", ready=False) is False


def test_canonical_bootstrap_requires_issue_to_match_persisted_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept only the selected issue, never a command-supplied alternate issue.

    Args:
        monkeypatch: Replaces repository and binding access boundaries.
    """
    # Expose only the expected root/task/bindings path and one persisted assignment.
    root = CHECKOUT
    key = core.participant_key(base("read"))
    opened: list[str] = []

    class Node:
        """Expose a direct three-level root/task/bindings handle chain."""

        def __init__(self, level: int = 0) -> None:
            """Select root, task, or binding lookup.

            Args:
                level: Root, task, or bindings depth in the fake directory chain.
            """
            self.level = level

        def __enter__(self) -> Node:
            """Hold the modeled opened directory.

            Returns:
                This directory node.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled directory.

            Args:
                _args: Context manager exception fields, unused by this fake.
            """

        def child(self, name: str) -> Node:
            """Require direct task then binding traversal.

            Args:
                name: Child directory requested by the bootstrap parser.

            Returns:
                A node at the next directory depth.
            """
            # Reject traversal outside the two assignment directories.
            opened.append(name)
            assert name == (".task" if self.level == 0 else ".bindings")
            return Node(self.level + 1)

        def json(self, name: str) -> dict[str, str]:
            """Read only the persisted assignment for the actual session.

            Args:
                name: Assignment filename for the actual session.

            Returns:
                The bound shorthand issue ID.
            """
            # The parser may read only the actual session's assignment.
            assert (self.level, name) == (2, key + ".assignment.json")
            return {"issue_id": "AGENT-30"}

    # Compare an exact issue request with a command-supplied foreign issue.
    monkeypatch.setattr(common.core, "repository", lambda _path: (root, None, [root]))
    monkeypatch.setattr(common.core.Directory, "absolute", lambda _path: Node())
    event = {"cwd": str(CHECKOUT), "session_id": "session"}
    matching = common.bootstrap_command(base("read", issue_id="AGENT-30"), "codex")
    foreign = common.bootstrap_command(base("read", issue_id="AGENT-31"), "codex")
    assert common.canonical_bootstrap(event, matching, "codex", ready=False) is True
    assert common.canonical_bootstrap(event, foreign, "codex", ready=False) is False
    assert opened == [".task", ".bindings", ".task", ".bindings"]


class AssignmentDirectory:
    """Expose one persisted session assignment through a fake directory chain."""

    def __enter__(self) -> AssignmentDirectory:
        """Hold the modeled opened directory.

        Returns:
            This directory node.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """Release the modeled directory.

        Args:
            _args: Context manager exception fields, unused by this fake.
        """

    def child(self, _name: str) -> AssignmentDirectory:
        """Return the same node for task and binding traversal.

        Args:
            _name: Child directory requested by the bootstrap parser.

        Returns:
            This directory node.
        """
        return self

    def json(self, _name: str) -> dict[str, str]:
        """Return the persisted assignment for the observed session.

        Args:
            _name: Assignment filename for the actual session.

        Returns:
            The bound shorthand issue ID.
        """
        return {"issue_id": "AGENT-30"}


@pytest.mark.parametrize("ready", [False, True])
def test_canonical_bootstrap_admits_exact_issue_level_diagnose(
    monkeypatch: pytest.MonkeyPatch, ready: bool
) -> None:
    """Admit the read-only issue diagnostic only for the assigned issue and exact shape.

    Args:
        monkeypatch: Replaces repository and binding access boundaries.
        ready: Whether ordinary readiness was already established.
    """
    # Bind the observed session to AGENT-30 through a persisted assignment.
    monkeypatch.setattr(common.core, "repository", lambda _path: (CHECKOUT, None, [CHECKOUT]))
    monkeypatch.setattr(common.core.Directory, "absolute", lambda _path: AssignmentDirectory())
    event = {"cwd": str(CHECKOUT), "session_id": "session"}
    issue = {"repo_id": "repo", "issue_id": "AGENT-30"}

    def admitted(request: dict[str, Any]) -> bool:
        """Evaluate one diagnose request through the canonical command parser.

        Args:
            request: Lifecycle request encoded into the shell command.

        Returns:
            Whether the hook would admit the command.
        """
        command = common.bootstrap_command(request, "codex")
        return common.canonical_bootstrap(event, command, "codex", ready=ready)

    # The issue shape with or without the session generation is admitted.
    assert admitted(base("diagnose", **issue)) is True
    assert admitted(base("diagnose", **issue, binding_generation=3)) is True
    # A foreign issue, a partial issue shape, or any extra field stays denied.
    assert admitted(base("diagnose", repo_id="repo", issue_id="AGENT-31")) is False
    assert admitted(base("diagnose", issue_id="AGENT-30")) is False
    for extra in [
        {"issue_uuid": "uuid"},
        {"expected_revision": 1},
        {"main_worktree": str(CHECKOUT)},
        {"startup": True},
    ]:
        # Each field outside the documented diagnostic shape must be rejected.
        assert admitted(base("diagnose", **issue, **extra)) is False, extra


def test_canonical_bootstrap_rejects_foreign_worktree_before_binding_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject commands redirected to another Git worktree before reading assignment.

    Args:
        monkeypatch: Replaces repository and binding access boundaries.
    """
    # Detect a command redirected away from the observed checkout.
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
    """Encode the bounded request and reject altered PowerShell command structure.

    Args:
        monkeypatch: Forces the Windows command transport branch.
    """
    # Encode a valid request under Windows quoting rules.
    monkeypatch.setattr(common, "os", SimpleNamespace(name="nt"))
    request = base("diagnose")
    command = common.bootstrap_command(request, "codex")
    assert command.startswith("& '")
    assert common.bootstrap_request(command, "codex", common.PYTHON, common.LIFECYCLE) == request
    # Reject appended commands and loss of the call operator.
    for changed in (command + " ; Write-Host bad", command.replace("& ", "", 1)):
        # Each altered command must fail the literal PowerShell grammar.
        with pytest.raises(ValueError, match="Noncanonical PowerShell command"):
            common.bootstrap_request(changed, "codex", common.PYTHON, common.LIFECYCLE)


def test_windows_bootstrap_rejects_unsafe_literal_argv_and_wrong_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows bootstrap accepts only the exact encoded entry and safe literal argv.

    Args:
        monkeypatch: Forces Windows transport and an unsafe interpreter path.
    """
    # Build the canonical Windows command before altering individual tokens.
    monkeypatch.setattr(common, "os", SimpleNamespace(name="nt"))
    request = base("diagnose")
    command = common.bootstrap_command(request, "codex")
    # Wrong entry switch and malformed quoting must fail parsing.
    with pytest.raises(ValueError, match="Unexpected Windows bootstrap entry"):
        common.bootstrap_request(
            command.replace("--request-base64", "--other"),
            "codex",
            common.PYTHON,
            common.LIFECYCLE,
        )
    # A quote injected into the interpreter token cannot be safely parsed.
    with pytest.raises(ValueError, match="Unsupported PowerShell quoting"):
        common.bootstrap_request(
            command.replace(common.PYTHON, common.PYTHON + '"'),
            "codex",
            common.PYTHON,
            common.LIFECYCLE,
        )
    # An unsafe interpreter path cannot be emitted as a command argument.
    monkeypatch.setattr(common, "PYTHON", 'unsafe"python')
    # Failure must happen at command construction, before any shell runs.
    with pytest.raises(ValueError, match="Unsupported PowerShell argument"):
        common.bootstrap_command(request, "codex")
