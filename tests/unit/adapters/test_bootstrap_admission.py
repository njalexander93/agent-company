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


SELF_KEY = core.participant_key(base("scope"))
READER_KEY = "f" * 64
REFRESHED = "b" * 64


def packet_ref(identifier: str, locator: str, reader: str = SELF_KEY) -> dict[str, Any]:
    """Build one committed packet reference with a fixed original digest.

    Args:
        identifier: Reference ID unique within the packet.
        locator: Managed or absolute source locator.
        reader: Participant key the reference is scoped to.

    Returns:
        A complete packet reference.
    """
    return {
        "id": identifier,
        "locator": locator,
        "sha256": "a" * 64,
        "required": True,
        "authority": "task-workspace",
        "reason": "startup",
        "stage": "planning",
        "reader": reader,
    }


CURRENT = [packet_ref("roadmap", "roadmap.md"), packet_ref("procedure", "/checkout/AGENTS.md")]


class IssueControl:
    """Expose a lockable issue control directory for the self-refresh state read."""

    def __enter__(self) -> IssueControl:
        """Hold the modeled control directory or lock.

        Returns:
            This control node.
        """
        return self

    def __exit__(self, *_args: object) -> None:
        """Release the modeled control directory or lock.

        Args:
            _args: Context manager exception fields, unused by this fake.
        """

    def lock(self) -> IssueControl:
        """Model the issue lock as a reentrant no-op.

        Returns:
            This control node.
        """
        return self


class StateStore(IssueControl):
    """Model a registered store whose issues directory yields one control node."""

    def __init__(self, _request: dict[str, Any]) -> None:
        """Accept the scope request as the core Store would.

        Args:
            _request: Decoded scope request, unused by this fake.
        """
        self.issues = SimpleNamespace(child=lambda _identifier: IssueControl())


def install_state(monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]) -> None:
    """Expose one assignment and one committed issue state to the bootstrap parser.

    Args:
        monkeypatch: Replaces repository, binding and store boundaries.
        state: Committed issue state returned by the fake issue.
    """
    # Bind the session to AGENT-30 and serve the supplied committed state.
    monkeypatch.setattr(common.core, "repository", lambda _path: (CHECKOUT, None, [CHECKOUT]))
    monkeypatch.setattr(common.core.Directory, "absolute", lambda _path: AssignmentDirectory())
    monkeypatch.setattr(common.core, "Store", StateStore)
    monkeypatch.setattr(
        common.core,
        "Issue",
        lambda *_args: SimpleNamespace(recover=lambda: None, committed_state=lambda: state),
    )


def coordinator_state(packet: list[dict[str, Any]] | None = CURRENT) -> dict[str, Any]:
    """Build committed state where the observed session coordinates the issue.

    Args:
        packet: The coordinator's committed packet, or None when unscoped.

    Returns:
        A minimal committed state with coordinator and reader participants.
    """
    return {
        "coordinator": SELF_KEY,
        "participants": {
            SELF_KEY: {"packet": packet, "ack": None, "status": "attached"},
            READER_KEY: {"packet": [packet_ref("roadmap", "roadmap.md", READER_KEY)]},
        },
    }


def refresh(packet: list[dict[str, Any]], **extra: object) -> dict[str, Any]:
    """Build a self-refresh scope request with the supplied packet.

    Args:
        packet: Proposed packet references.
        extra: Additional or replacement request fields.

    Returns:
        A scope request for the assigned issue.
    """
    fields: dict[str, object] = {
        "repo_id": "repo",
        "issue_id": "AGENT-30",
        "binding_generation": 1,
        "expected_revision": 7,
        "target_participant": SELF_KEY,
        "packet": packet,
    }
    return base("scope", **{**fields, **extra})


def refreshed(packet: list[dict[str, Any]] = CURRENT) -> list[dict[str, Any]]:
    """Replace every digest in a packet with the refreshed digest.

    Args:
        packet: Packet references to copy.

    Returns:
        Copies of the references with only sha256 changed.
    """
    return [{**ref, "sha256": REFRESHED} for ref in packet]


def admitted_unready(request: dict[str, Any]) -> bool:
    """Evaluate one request through the canonical parser for an unready session.

    Args:
        request: Lifecycle request encoded into the shell command.

    Returns:
        Whether the hook would admit the command before readiness.
    """
    event = {"cwd": str(CHECKOUT), "session_id": "session"}
    command = common.bootstrap_command(request, "codex")
    return common.canonical_bootstrap(event, command, "codex", ready=False)


def test_unready_coordinator_self_refresh_scope_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A coordinator may re-scope its own packet with only refreshed digests.

    Args:
        monkeypatch: Replaces repository, binding and store boundaries.
    """
    # The digest-only refresh of the coordinator's own current packet is admitted.
    install_state(monkeypatch, coordinator_state())
    assert admitted_unready(refresh(refreshed())) is True
    # An unchanged digest is still a valid digest-only shape.
    assert admitted_unready(refresh([dict(ref) for ref in CURRENT])) is True


@pytest.mark.parametrize(
    "packet",
    [
        pytest.param(refreshed(CURRENT[:1]), id="removed-reference"),
        pytest.param(
            refreshed([*CURRENT, packet_ref("extra", "context/note.md")]), id="added-reference"
        ),
        pytest.param(refreshed(CURRENT[::-1]), id="reordered-references"),
        *[
            pytest.param([{**refreshed()[0], field: value}, refreshed()[1]], id="changed-" + field)
            for field, value in (
                ("locator", "context/other.md"),
                ("reader", READER_KEY),
                ("required", False),
                ("authority", "repository-governing"),
                ("reason", "other"),
                ("stage", "implementation"),
                ("id", "renamed"),
            )
        ],
        pytest.param([{**refreshed()[0], "sha256": "not-a-digest"}, refreshed()[1]], id="bad-sha"),
        pytest.param([{**refreshed()[0], "sha256": "A" * 64}, refreshed()[1]], id="upper-sha"),
        pytest.param([{**refreshed()[0], "extra": "field"}, refreshed()[1]], id="extra-ref-field"),
        pytest.param("not-a-list", id="nonlist-packet"),
    ],
)
def test_unready_self_refresh_rejects_changed_packet_shape(
    monkeypatch: pytest.MonkeyPatch, packet: Any
) -> None:
    """Any packet change beyond reference digests keeps the scope denied.

    Args:
        monkeypatch: Replaces repository, binding and store boundaries.
        packet: Proposed packet that differs from the committed one beyond digests.
    """
    # Only sha256 may differ from the committed coordinator packet.
    install_state(monkeypatch, coordinator_state())
    assert admitted_unready(refresh(packet)) is False


def test_unready_self_refresh_rejects_other_target_extra_fields_and_owned_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The self-refresh cannot scope another key, assign paths or carry extra fields.

    Args:
        monkeypatch: Replaces repository, binding and store boundaries.
    """
    # Targeting another participant with the same packet is still a coordinator scope.
    install_state(monkeypatch, coordinator_state())
    other = refresh(refreshed(), target_participant=READER_KEY)
    assert admitted_unready(other) is False
    # Owned paths and arbitrary fields are outside the self-refresh shape.
    assert admitted_unready(refresh(refreshed(), owned_paths=["context/note.md"])) is False
    assert admitted_unready(refresh(refreshed(), arbitrary="injection")) is False
    # A foreign issue fails the assignment check after the shape check.
    assert admitted_unready(refresh(refreshed(), issue_id="AGENT-31")) is False


def test_unready_self_refresh_rejects_non_coordinator_and_unscoped_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reader or a coordinator without a committed packet cannot self-refresh.

    Args:
        monkeypatch: Replaces repository, binding and store boundaries.
    """
    # A reader holding its own packet is not the coordinator.
    reader = coordinator_state()
    reader["coordinator"] = READER_KEY
    install_state(monkeypatch, reader)
    assert admitted_unready(refresh(refreshed())) is False
    # A coordinator with no committed packet has nothing to refresh.
    install_state(monkeypatch, coordinator_state(packet=None))
    assert admitted_unready(refresh(refreshed())) is False
    # A coordinator absent from the participants map is denied as well.
    absent = coordinator_state()
    del absent["participants"][SELF_KEY]
    install_state(monkeypatch, absent)
    assert admitted_unready(refresh(refreshed())) is False


def test_ready_scope_keeps_its_existing_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    """After readiness the self-refresh check does not narrow ordinary coordinator scope.

    Args:
        monkeypatch: Replaces repository, binding and store boundaries.
    """
    # A ready coordinator may still scope another participant with owned paths.
    install_state(monkeypatch, coordinator_state())
    request = refresh(refreshed(), target_participant=READER_KEY, owned_paths=["context/n.md"])
    event = {"cwd": str(CHECKOUT), "session_id": "session"}
    command = common.bootstrap_command(request, "codex")
    assert common.canonical_bootstrap(event, command, "codex", ready=True) is True
