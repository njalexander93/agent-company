"""Model startup ordering after an independently verified provider ticket."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_company.adapters import startup
from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


class Context:
    """Provide only the context manager shape used by the startup final read."""

    def __enter__(self) -> Context:
        """Return the modeled open handle."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Finish a modeled read-only handle lifetime."""

    def child(self, _name: str) -> Context:
        """Expose the modeled issue control directory."""
        return self

    def lock(self) -> Context:
        """Expose a modeled held issue lock."""
        return self


class Store(Context):
    """Supply a read-only issue state after startup completes."""

    def __init__(self, _request: dict[str, object], coordinator: str) -> None:
        """Retain the independently expected coordinator identity."""
        self.issues = self
        self.coordinator = coordinator


class Issue:
    """Expose only the final coordinator identity required by startup."""

    def __init__(self, store: Store, _control: Context, _identifier: str) -> None:
        """Read the modeled store state."""
        self.store = store

    def recover(self) -> None:
        """Model successful read-only recovery."""

    def committed_state(self) -> dict[str, str]:
        """Return the persisted coordinator identity."""
        return {"coordinator": self.store.coordinator}


def test_read_file_opens_only_parent_and_reads_exact_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Use the lifecycle no-follow parent handle for one assigned source file."""
    opened: list[Path] = []
    reads: list[str] = []

    class Parent(Context):
        """Expose a bounded read from the selected parent directory."""

        def read(self, name: str) -> bytes:
            """Capture the exact leaf name without path traversal."""
            reads.append(name)
            return b"approved source"

    monkeypatch.setattr(
        startup.core.Directory,
        "absolute",
        lambda path: opened.append(path) or Parent(),
    )
    source = tmp_path / "checkout/docs/AGENTS.md"
    assert startup._read_file(source) == b"approved source"
    assert opened == [source.parent]
    assert reads == ["AGENTS.md"]


def test_startup_call_preserves_core_failure_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected lifecycle transition retains its exact diagnostic code."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        startup.core,
        "execute",
        lambda request: calls.append(request) or {"ok": False, "code": "SOURCE_STALE"},
    )
    with pytest.raises(core.WorkspaceError) as captured:
        startup._call({"operation": "read"})
    assert captured.value.code == "SOURCE_STALE"
    assert len(calls) == 1
    assert calls[0]["operation"] == "read"
    assert calls[0]["schema_version"] == 1


def test_initial_packet_skips_absent_optional_checkout_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The roadmap remains required even when checkout rule files are absent."""
    checkout, main = tmp_path / "checkout", tmp_path / "main"
    checkout.mkdir()
    main.mkdir()
    roadmap = main / ".task/AGENT-30/roadmap.md"
    monkeypatch.setattr(
        startup,
        "_read_file",
        lambda path: b"roadmap" if path == roadmap else pytest.fail("optional read"),
    )
    packet = startup._initial_packet(checkout, main, "AGENT-30", "reader-key")
    assert len(packet) == 1
    assert packet[0]["id"] == "roadmap"
    assert packet[0]["sha256"] == core.sha(b"roadmap")


def test_initial_packet_records_exact_canonical_source_digests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bind the startup packet to the actual roadmap and checkout rule bytes."""
    checkout, main = tmp_path / "checkout", tmp_path / "main"
    (checkout / "docs/runtime").mkdir(parents=True)
    (main / ".task/AGENT-30").mkdir(parents=True)
    (checkout / "AGENTS.md").touch()
    (checkout / "docs/runtime/contributor-workflow.md").touch()
    bytes_by_path = {
        main / ".task/AGENT-30/roadmap.md": b"roadmap v1",
        checkout / "AGENTS.md": b"rules v1",
        checkout / "docs/runtime/contributor-workflow.md": b"procedure v1",
    }
    monkeypatch.setattr(startup, "_read_file", lambda path: bytes_by_path[path])
    packet = startup._initial_packet(checkout, main, "AGENT-30", "reader-key")
    assert [ref["id"] for ref in packet] == ["roadmap", "contributors", "procedure"]
    assert [ref["sha256"] for ref in packet] == [
        core.sha(b"roadmap v1"),
        core.sha(b"rules v1"),
        core.sha(b"procedure v1"),
    ]
    assert all(ref["reader"] == "reader-key" and ref["required"] is True for ref in packet)
    assert packet[0]["locator"] == "roadmap.md"


def test_start_creates_scope_then_reads_bytes_before_acknowledging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Require source-byte verification before the packet acknowledgment call."""
    key = core.participant_key({"host": "codex", "session_id": "actual-session"})
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    read_path = checkout / ".task" / "AGENT-30" / "roadmap.md"
    calls: list[str] = []
    references = [
        {
            "locator": "roadmap.md",
            "sha256": core.sha(b"approved roadmap"),
            "available": True,
        }
    ]

    def call(request: dict[str, Any]) -> dict[str, Any]:
        """Return the bounded lifecycle state transitions for one new issue."""
        operation = request["operation"]
        calls.append(operation)
        if operation == "register":
            assert request["main_worktree"] == str(checkout)
            return {"ok": True, "repo_id": "repo"}
        if operation == "diagnose":
            return {"ok": True, "code": "ABSENT", "revision": 1}
        if operation == "create":
            assert request["coordinator"] == key
            return {"ok": True, "binding_generation": 1}
        if operation == "scope":
            assert request["target_participant"] == key
            assert request["packet"][0]["sha256"] == core.sha(b"approved roadmap")
            return {"ok": True}
        if operation == "read":
            return {"ok": True, "references": references, "revision": 2, "packet_digest": "digest"}
        if operation == "acknowledge":
            assert calls[-2] == "source-bytes-read"
            assert request["packet_digest"] == "digest"
            return {"ok": True}
        if operation == "ready":
            return {"ok": True, "code": "OK"}
        pytest.fail(f"unexpected operation {operation}")

    monkeypatch.setattr(startup.core, "repository", lambda _cwd: (checkout, None, [checkout]))
    monkeypatch.setattr(startup, "_call", call)
    monkeypatch.setattr(
        startup, "_initial_packet", lambda *_args: [{"sha256": core.sha(b"approved roadmap")}]
    )

    def read_file(path: Path) -> bytes:
        """Return source bytes while recording their read order."""
        assert path == read_path
        calls.append("source-bytes-read")
        return b"approved roadmap"

    monkeypatch.setattr(startup, "_read_file", read_file)
    monkeypatch.setattr(startup.core, "Store", lambda request: Store(request, key))
    monkeypatch.setattr(startup.core, "Issue", Issue)
    result = startup.start(
        {"cwd": str(checkout), "session_id": "actual-session"},
        {"id": "AGENT-30", "uuid": "verified-uuid"},
    )
    assert result["participant_id"] == result["coordinator"] == key
    assert calls == [
        "register",
        "diagnose",
        "create",
        "diagnose",
        "scope",
        "read",
        "source-bytes-read",
        "acknowledge",
        "ready",
    ]


def test_start_refuses_stale_source_without_acknowledgment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stop when delivered bytes differ from the assigned digest."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls: list[str] = []

    def call(request: dict[str, Any]) -> dict[str, Any]:
        """Expose a new issue up to the source read."""
        operation = request["operation"]
        calls.append(operation)
        if operation == "register":
            return {"ok": True, "repo_id": "repo"}
        if operation == "diagnose":
            return {"ok": True, "code": "ABSENT", "revision": 1}
        if operation == "create":
            return {"ok": True, "binding_generation": 1}
        if operation == "scope":
            return {"ok": True}
        if operation == "read":
            return {
                "ok": True,
                "references": [
                    {"locator": "roadmap.md", "sha256": core.sha(b"expected"), "available": True}
                ],
                "revision": 2,
                "packet_digest": "digest",
            }
        pytest.fail("acknowledged stale source")

    monkeypatch.setattr(startup.core, "repository", lambda _cwd: (checkout, None, [checkout]))
    monkeypatch.setattr(startup, "_call", call)
    monkeypatch.setattr(startup, "_initial_packet", lambda *_args: [])
    monkeypatch.setattr(startup, "_read_file", lambda _path: b"different")
    with pytest.raises(core.WorkspaceError) as captured:
        startup.start(
            {"cwd": str(checkout), "session_id": "actual-session"},
            {"id": "AGENT-30", "uuid": "verified-uuid"},
        )
    assert captured.value.code == "SOURCE_STALE"
    assert "acknowledge" not in calls


def test_start_existing_reader_joins_without_replacing_coordinator_packet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Join an unassigned reader through the core-selected roadmap packet only."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    reader = core.participant_key({"host": "codex", "session_id": "reader"})
    coordinator = core.participant_key({"host": "codex", "session_id": "owner"})
    operations: list[str] = []
    state: dict[str, Any] = {
        "issue_uuid": "verified-uuid",
        "revision": 4,
        "coordinator": coordinator,
        "participants": {reader: {"generation": 2, "packet": [{"locator": "roadmap.md"}]}},
        "assignments": {},
        "files": {"roadmap.md": core.sha(b"roadmap")},
    }

    class ExistingStore(Context):
        """Expose one committed existing issue to the startup reader."""

        def __init__(self, _request: dict[str, object]) -> None:
            """Select the shared issue control handle."""
            self.issues = self

    class ExistingIssue:
        """Return the configured committed state and verify-file marker."""

        def __init__(self, _store: ExistingStore, _control: Context, _identifier: str) -> None:
            """Keep the configured shared state."""

        def recover(self) -> None:
            """Model completed transaction recovery."""

        def committed_state(self) -> dict[str, Any]:
            """Expose the current committed participant map."""
            return state

        def files(self) -> dict[str, bytes]:
            """Confirm the manifest before source acknowledgment."""
            operations.append("verify-files")
            return {"roadmap.md": b"roadmap"}

    def call(request: dict[str, Any]) -> dict[str, Any]:
        """Model registration, join, read, and acknowledgment in order."""
        operation = request["operation"]
        operations.append(operation)
        if operation == "register":
            return {"ok": True, "repo_id": "repo"}
        if operation == "diagnose":
            return {"ok": True, "code": "PRESENT", "revision": 4}
        if operation == "join":
            assert request["expected_revision"] == 4
            state["participants"][reader] = {
                "generation": 2,
                "packet": [
                    {
                        "locator": "roadmap.md",
                        "reason": "issue-resume",
                        "sha256": core.sha(b"roadmap"),
                    }
                ],
            }
            return {"ok": True, "binding_generation": 2}
        if operation == "read":
            return {"ok": True, "references": [], "revision": 5, "packet_digest": "digest"}
        if operation in {"acknowledge", "ready"}:
            return {"ok": True}
        pytest.fail(f"unexpected lifecycle operation {operation}")

    # First state read has no reader; join then publishes the core-selected participant.
    initial_state = state.copy()
    initial_state["participants"] = {}
    reads = 0

    def committed_state(self: ExistingIssue) -> dict[str, Any]:
        """Expose absent reader only before the join operation."""
        nonlocal reads
        reads += 1
        return initial_state if reads == 1 else state

    monkeypatch.setattr(ExistingIssue, "committed_state", committed_state)
    monkeypatch.setattr(startup.core, "repository", lambda _cwd: (checkout, None, [checkout]))
    monkeypatch.setattr(startup, "_call", call)
    monkeypatch.setattr(startup.core, "Store", ExistingStore)
    monkeypatch.setattr(startup.core, "Issue", ExistingIssue)
    result = startup.start(
        {"cwd": str(checkout), "session_id": "reader"},
        {"id": "AGENT-30", "uuid": "verified-uuid"},
    )
    assert result["coordinator"] == coordinator
    assert result["participant_id"] == reader
    assert operations == [
        "register",
        "diagnose",
        "join",
        "verify-files",
        "read",
        "acknowledge",
        "ready",
    ]


@pytest.mark.parametrize(
    ("mode", "expected_operations", "expected_error"),
    [
        (
            "coordinator-missing",
            [
                "register",
                "diagnose",
                "resume",
                "verify-files",
                "diagnose",
                "scope",
                "read",
                "acknowledge",
                "ready",
            ],
            None,
        ),
        (
            "coordinator-stale",
            [
                "register",
                "diagnose",
                "resume",
                "verify-files",
                "scope",
                "read",
                "acknowledge",
                "ready",
            ],
            None,
        ),
        (
            "coordinator-optional",
            ["register", "diagnose", "resume", "verify-files", "read", "acknowledge", "ready"],
            None,
        ),
        (
            "coordinator-unowned",
            ["register", "diagnose", "resume", "verify-files", "read", "acknowledge", "ready"],
            None,
        ),
        (
            "reader-stale",
            [
                "register",
                "diagnose",
                "resume",
                "verify-files",
                "join",
                "read",
                "acknowledge",
                "ready",
            ],
            None,
        ),
        ("reader-missing", ["register", "diagnose", "resume", "verify-files"], "SCOPE_MISSING"),
        ("uuid-mismatch", ["register", "diagnose"], "ISSUE_MISMATCH"),
    ],
)
def test_start_existing_assignment_respects_coordinator_packet_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    expected_operations: list[str],
    expected_error: str | None,
) -> None:
    """Refresh only coordinator-owned scope and reject missing reader scope or wrong UUID."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    key = core.participant_key({"host": "codex", "session_id": "actual"})
    coordinator = key if mode.startswith("coordinator") or mode == "uuid-mismatch" else "other"
    packet = (
        None
        if "missing" in mode
        else [
            {
                "id": "roadmap",
                "locator": "roadmap.md",
                "sha256": core.sha(b"old"),
                "reason": "issue-startup",
            },
            {
                "id": "rules",
                "locator": str(checkout / "AGENTS.md"),
                "sha256": core.sha(b"rules"),
                "reason": "issue-startup",
            },
        ]
    )
    if mode == "coordinator-optional":
        packet[0]["sha256"] = core.sha(b"new")
    if mode == "reader-stale":
        packet = [
            {
                "id": "roadmap",
                "locator": "roadmap.md",
                "sha256": core.sha(b"old"),
                "reason": "issue-resume",
            }
        ]
    state: dict[str, Any] = {
        "issue_uuid": "foreign" if mode == "uuid-mismatch" else "verified-uuid",
        "revision": 4,
        "coordinator": coordinator,
        "participants": {key: {"generation": 2, "packet": packet}},
        "owners": {"roadmap.md": "foreign" if mode == "coordinator-unowned" else coordinator},
        "files": {"roadmap.md": core.sha(b"new")},
    }
    operations: list[str] = []

    class ExistingStore(Context):
        """Expose one existing issue control handle."""

        def __init__(self, _request: dict[str, object]) -> None:
            """Select the issue handle."""
            self.issues = self

    class ExistingIssue:
        """Expose the exact committed state after modeled recovery."""

        def __init__(self, _store: ExistingStore, _control: Context, _identifier: str) -> None:
            """Retain the existing issue binding."""

        def recover(self) -> None:
            """Model completed control recovery."""

        def committed_state(self) -> dict[str, Any]:
            """Return the configured existing state."""
            return state

        def files(self) -> dict[str, bytes]:
            """Verify the committed manifest before packet decisions."""
            operations.append("verify-files")
            return {"roadmap.md": b"new"}

    def call(request: dict[str, Any]) -> dict[str, Any]:
        """Capture only the startup lifecycle requests and their ordered effects."""
        operation = request["operation"]
        operations.append(operation)
        if operation == "register":
            return {"ok": True, "repo_id": "repo"}
        if operation == "diagnose":
            return {"ok": True, "code": "PRESENT", "revision": 4}
        if operation == "resume":
            return {"ok": True, "binding_generation": 2}
        if operation == "join":
            assert request["expected_revision"] == 4
            state["participants"][key]["packet"][0]["sha256"] = core.sha(b"new")
            return {"ok": True, "binding_generation": 2}
        if operation == "scope":
            assert request["target_participant"] == key
            state["participants"][key]["packet"] = request["packet"]
            return {"ok": True}
        if operation == "read":
            references = (
                [{"locator": str(checkout / "optional.md"), "available": False}]
                if mode == "coordinator-optional"
                else []
            )
            return {"ok": True, "references": references, "revision": 5, "packet_digest": "digest"}
        if operation in {"acknowledge", "ready"}:
            return {"ok": True}
        pytest.fail(f"unexpected lifecycle operation {operation}")

    monkeypatch.setattr(startup.core, "repository", lambda _cwd: (checkout, None, [checkout]))
    monkeypatch.setattr(startup.core, "Store", ExistingStore)
    monkeypatch.setattr(startup.core, "Issue", ExistingIssue)
    monkeypatch.setattr(startup, "_call", call)
    monkeypatch.setattr(
        startup,
        "_initial_packet",
        lambda *_args: [{"id": "roadmap", "locator": "roadmap.md", "sha256": core.sha(b"new")}],
    )
    if expected_error:
        with pytest.raises(core.WorkspaceError) as captured:
            startup.start(
                {"cwd": str(checkout), "session_id": "actual"},
                {"id": "AGENT-30", "uuid": "verified-uuid"},
            )
        assert captured.value.code == expected_error
    else:
        result = startup.start(
            {"cwd": str(checkout), "session_id": "actual"},
            {"id": "AGENT-30", "uuid": "verified-uuid"},
        )
        assert result["participant_id"] == key
        assert result["coordinator"] == coordinator
        expected_digest = core.sha(b"old" if mode == "coordinator-unowned" else b"new")
        assert state["participants"][key]["packet"][0]["sha256"] == expected_digest
    assert operations == expected_operations
