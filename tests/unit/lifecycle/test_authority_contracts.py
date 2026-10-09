"""Check authority and source-reference decisions without persistent state."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def request() -> dict[str, object]:
    """Build the bound request used by authority checks.

    Returns:
        Request mapping with the selected operation and overrides.
    """
    return {
        "host": "codex",
        "session_id": "session",
        "binding_generation": 2,
        "operation": "resume",
    }


def state() -> dict[str, object]:
    """Build one ready coordinator at revision seven.

    Returns:
        Issue state configured for the contract case.
    """
    # Derive the coordinator key from the bound host and session.
    key = core.participant_key(request())
    return {
        "coordinator": key,
        "revision": 7,
        "participants": {
            key: {"generation": 2, "status": "ready", "pending": {}, "packet": [], "ack": None}
        },
    }


def test_authorize_returns_existing_participant_without_mutation() -> None:
    """Authorize returns existing participant without mutation."""
    # Snapshot the issue before checking that authorization is read-only.
    current = state()
    before = copy.deepcopy(current)
    # Resolve the existing coordinator binding.
    key, participant = core.authorize(current, request(), coordinator=True)  # type: ignore[arg-type]
    # Confirm the returned participant is the same state object and state is unchanged.
    assert key == current["coordinator"]
    assert participant is current["participants"][key]  # type: ignore[index]
    assert current == before


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"session_id": "other"}, "BINDING_MISSING"),
        ({"binding_generation": 1}, "STALE_BINDING"),
        ({"binding_generation": None}, "STALE_BINDING"),
    ],
)
def test_authorize_rejects_missing_or_stale_binding(
    change: dict[str, object], expected: str
) -> None:
    """Authorize rejects missing or stale binding.

    Args:
        change: Proposed state change.
        expected: Expected result for this input.
    """
    # Snapshot the issue before checking that authorization is read-only.
    current = state()
    before = copy.deepcopy(current)
    # Attempt authorization with the modified binding or authority state.
    with pytest.raises(core.WorkspaceError) as captured:
        core.authorize(current, {**request(), **change})  # type: ignore[arg-type]
    # Match the contractual rejection and preserve the original state.
    assert captured.value.code == expected
    assert current == before


def test_authorize_limits_maintenance_and_coordinator_authority() -> None:
    """Authorize limits maintenance and coordinator authority."""
    # Move the existing coordinator into maintenance status.
    current = state()
    key = current["coordinator"]
    participant = current["participants"][key]  # type: ignore[index]
    participant["status"] = "maintenance"
    # Attempt authorization with the modified binding or authority state.
    with pytest.raises(core.WorkspaceError) as captured:
        core.authorize(current, request())  # type: ignore[arg-type]
    # Confirm the rejected operation reports STALE_BINDING.
    assert captured.value.code == "STALE_BINDING"
    assert core.authorize(current, request(), maintenance=True)[0] == key  # type: ignore[arg-type]
    # Remove coordinator authority while retaining the participant binding.
    current["coordinator"] = "another-participant"
    # Attempt authorization with the modified binding or authority state.
    with pytest.raises(core.WorkspaceError) as captured:
        core.authorize(current, request(), maintenance=True, coordinator=True)  # type: ignore[arg-type]
    # Confirm the rejected operation reports NOT_OWNER.
    assert captured.value.code == "NOT_OWNER"


def test_expected_requires_exact_revision() -> None:
    """Expected requires exact revision."""
    # Start from a committed revision of seven.
    current = state()
    # Accept the exact current revision.
    assert core.expected(current, {"expected_revision": 7}) is None  # type: ignore[arg-type]
    # Reject missing, stale, string, and future revision claims.
    for supplied in (None, 6, "7", 8):
        # Validate this mismatched revision against committed state.
        with pytest.raises(core.WorkspaceError) as captured:
            core.expected(current, {"expected_revision": supplied})  # type: ignore[arg-type]
        # Confirm the rejected operation reports REVISION_CONFLICT.
        assert captured.value.code == "REVISION_CONFLICT"


def reference(**changes: object) -> dict[str, object]:
    """Build a required reader-scoped note reference.

    Args:
        **changes: State fields overridden for this scenario.

    Returns:
        Reader-specific source reference with the current digest.
    """
    return {
        "id": "issue",
        "locator": "context/issue.md",
        "sha256": core.sha(b"issue bytes"),
        "required": True,
        "authority": "approved-assignment",
        "reason": "bounded-step",
        "stage": "execution",
        "reader": "participant-key",
        **changes,
    }


def test_evidence_accepts_attributable_digest_and_rejects_incomplete_records() -> None:
    """Evidence accepts attributable digest and rejects incomplete records."""
    # Create an attributable evidence record with the expected digest.
    valid = [{"id": "review", "locator": "context/review.md", "sha256": core.sha(b"reviewed")}]  # type: ignore[var-annotated]
    # Accept complete evidence without changing its fields.
    assert core.evidence(valid) is valid  # type: ignore[arg-type]
    # Remove one required evidence field at a time.
    for bad in (None, [], valid * 17, [{**valid[0], "sha256": "x"}], [{"id": "x"}]):
        # Reject the incomplete evidence record.
        with pytest.raises(core.WorkspaceError) as captured:
            core.evidence(bad)  # type: ignore[arg-type]
        # Confirm the rejected operation reports EVIDENCE_REQUIRED.
        assert captured.value.code == "EVIDENCE_REQUIRED"


def test_validate_packet_accepts_exact_reader_scoped_reference() -> None:
    """Validate packet accepts exact reader scoped reference."""
    # Build a packet assigned to the exact reader and source bytes.
    packet = [reference()]
    # Confirm every reference stays within its assigned reader scope.
    assert core.validate_packet(packet) is packet  # type: ignore[arg-type]


def test_validate_packet_checks_every_relative_reference_before_return() -> None:
    """Multiple assigned note paths each pass the scope and path grammar."""
    # Build a packet assigned to the exact reader and source bytes.
    packet = [
        reference(id="first", locator="context/first.md"),
        reference(id="second", locator="context/second.md"),
    ]
    # Confirm every reference stays within its assigned reader scope.
    assert core.validate_packet(packet) is packet  # type: ignore[arg-type]


def test_validate_packet_preserves_absolute_repository_reference(tmp_path: Path) -> None:
    """An absolute governing source bypasses payload grammar but remains reader-scoped.

    Args:
        tmp_path: Disposable directory owned by this test.
    """
    # Build a packet assigned to the exact reader and source bytes.
    packet = [
        reference(id="rules", locator=str(tmp_path / "checkout/AGENTS.md")),
        reference(id="roadmap", locator="roadmap.md"),
    ]
    # Confirm every reference stays within its assigned reader scope.
    assert core.validate_packet(packet) is packet  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("packet", "code"),
    [
        ([reference(), reference()], "SCOPE_MISSING"),
        ([reference(required=1)], "SCOPE_MISSING"),
        ([reference(sha256="z" * 64)], "SCOPE_MISSING"),
        ([reference(locator="../escape.md")], "UNSAFE_PATH"),
        ([reference(reader="")], "INVALID_REQUEST"),
        ([reference(extra=True)], "SCOPE_MISSING"),
        (["not-a-reference"], "SCOPE_MISSING"),
    ],
)
def test_validate_packet_rejects_ambiguous_or_out_of_scope_source(
    packet: object, code: str
) -> None:
    """Validate packet rejects ambiguous or out of scope source.

    Args:
        packet: Reader packet installed for this scenario.
        code: Error or event code expected in this case.
    """
    # Reject the source whose path or reader breaks packet authority.
    with pytest.raises(core.WorkspaceError) as captured:
        core.validate_packet(packet)  # type: ignore[arg-type]
    # Match the contractual rejection and preserve the original state.
    assert captured.value.code == code


def test_packet_reads_reports_required_match_and_optional_loss() -> None:
    """Packet reads reports required match and optional loss."""
    # Give the participant one required and one optional source.
    required = reference()
    optional = reference(id="maybe", locator="context/maybe.md", required=False)
    participant = {"packet": [required, optional]}
    # Read both assigned sources against their recorded digests.
    result = core.packet_reads({}, participant, {"context/issue.md": b"issue bytes"})  # type: ignore[arg-type]
    assert [item["available"] for item in result] == [True, False]


def test_packet_reads_rejects_stale_required_bytes() -> None:
    """Packet reads rejects stale required bytes."""
    # Change the required source after the participant packet is pinned.
    participant = {"packet": [reference()]}
    # Reject stale required bytes while retaining optional diagnostics.
    with pytest.raises(core.WorkspaceError) as captured:
        core.packet_reads({}, participant, {"context/issue.md": b"altered"})  # type: ignore[arg-type]
    # Confirm the rejected operation reports SOURCE_STALE.
    assert captured.value.code == "SOURCE_STALE"


def test_packet_reads_uses_owned_external_parent_for_absolute_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Read only the exact assigned absolute source through its no-follow parent.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Record direct directory handles to prove no-follow source access.
    opened: list[Path] = []
    source = tmp_path / "checkout/AGENTS.md"

    class Parent:
        """Expose one validated external source file."""

        def __enter__(self) -> Parent:
            """Hold the modeled parent directory.

            Returns:
                This fake context manager for the enclosed operation.
            """
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled parent directory.

            Args:
                *_args: Exception details supplied by the context-manager protocol.
            """

        def read(self, name: str, limit: int) -> bytes:
            """Require the assigned leaf and bounded read.

            Args:
                name: Requested file, directory, or control-entry name.
                limit: Maximum size or count allowed by the operation.

            Returns:
                Bytes returned by the fake file or provider read.
            """
            # Accept only the expected source name from the fake directory.
            assert (name, limit) == ("AGENTS.md", core.MAX_FILE)
            return b"rules"

    # Route absolute source reads through the controlled no-follow directory.
    monkeypatch.setattr(core.Directory, "absolute", lambda path: opened.append(path) or Parent())
    ref = {
        "id": "rules",
        "locator": str(source),
        "sha256": core.sha(b"rules"),
        "required": True,
    }
    # Confirm only the assigned parent and file were opened.
    assert core.packet_reads({}, {"packet": [ref]}, {}) == [{**ref, "available": True}]  # type: ignore[arg-type]
    assert opened == [source.parent]


def test_packet_reads_marks_failed_optional_external_source_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep optional source loss visible while required loss blocks readiness.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
        tmp_path: Disposable directory owned by this test.
    """
    # Route absolute source reads through the controlled no-follow directory.
    monkeypatch.setattr(
        core.Directory,
        "absolute",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("external source")),
    )
    ref = {
        "id": "optional",
        "locator": str(tmp_path / "checkout/optional.md"),
        "sha256": core.sha(b"expected"),
        "required": False,
    }
    # Report the optional source as unavailable without blocking the required source.
    assert core.packet_reads({}, {"packet": [ref]}, {}) == [{**ref, "available": False}]  # type: ignore[arg-type]
    # Reject stale required bytes while retaining optional diagnostics.
    with pytest.raises(core.WorkspaceError) as captured:
        core.packet_reads({}, {"packet": [{**ref, "required": True}]}, {})  # type: ignore[arg-type]
    # Confirm the rejected operation reports SOURCE_STALE.
    assert captured.value.code == "SOURCE_STALE"


def test_packet_reads_requires_explicit_assigned_packet() -> None:
    """Do not infer permitted references from available payload files."""
    # Reject stale required bytes while retaining optional diagnostics.
    with pytest.raises(core.WorkspaceError) as captured:
        core.packet_reads({}, {"packet": None}, {"roadmap.md": b"present"})  # type: ignore[arg-type]
    # Confirm the rejected operation reports SCOPE_MISSING.
    assert captured.value.code == "SCOPE_MISSING"


def test_new_state_binds_only_explicit_coordinator() -> None:
    """New state binds only explicit coordinator."""

    class Store:
        """Fake store that exposes registration metadata for authority checks."""

        registration = {"repo_id": "repo"}

    # Supply a coordinator claim from a different participant.
    supplied = {
        **request(),
        "coordinator": core.participant_key(request()),
        "issue_id": "AGENT-30",
        "issue_uuid": "uuid",
    }
    # Create initial state using the explicitly supplied coordinator.
    initial = core.new_state(Store(), supplied)  # type: ignore[arg-type]
    # Confirm initial revision and coordinator binding match the request.
    assert initial["revision"] == 0
    assert initial["coordinator"] == supplied["coordinator"]
    assert initial["owners"] == {"roadmap.md": supplied["coordinator"]}
    assert initial["participants"] == {}
    assert initial["files"] == {}
    # Replace the coordinator claim with a foreign participant.
    supplied["coordinator"] = "other"
    # Reject a new state that would transfer coordinator authority.
    with pytest.raises(core.WorkspaceError) as captured:
        core.new_state(Store(), supplied)  # type: ignore[arg-type]
    # Confirm the rejected operation reports NOT_OWNER.
    assert captured.value.code == "NOT_OWNER"


def test_attach_creates_only_assigned_participant() -> None:
    """Create the coordinator binding while refusing an unassigned reader."""
    # Make an active, stored issue eligible for participant attach.
    current = state()
    current["disposition"] = "active"
    current["storage"] = "present"
    current["participants"] = {}
    # Derive the coordinator key from the bound host and session.
    key = core.participant_key(request())
    result = core.attach(current, request())  # type: ignore[arg-type]
    assert result == {"participant_id": key, "binding_generation": 1}
    assert current["participants"][key]["packet"] is None  # type: ignore[index]
    # Build an unassigned reader request against that issue.
    other = {**request(), "session_id": "reader"}
    # Attempt attach without the required assignment or valid binding.
    with pytest.raises(core.WorkspaceError) as captured:
        core.attach(current, other)  # type: ignore[arg-type]
    # Confirm the rejected operation reports SCOPE_MISSING.
    assert captured.value.code == "SCOPE_MISSING"


def test_attach_resumes_detached_participant_with_new_generation() -> None:
    """Fence an old detached binding and require packet acknowledgment again."""
    # Make an active, stored issue eligible for participant attach.
    current = state()
    current["disposition"] = "active"
    current["storage"] = "present"
    key = current["coordinator"]
    member = current["participants"][key]  # type: ignore[index]
    # Refresh the detached participant binding before attach.
    member.update(status="detached", ack="old-digest")
    result = core.attach(current, request())  # type: ignore[arg-type]
    assert result["binding_generation"] == 3
    assert (member["status"], member["ack"]) == ("attached", None)


def test_attach_rejects_terminal_or_stale_binding_without_mutation() -> None:
    """Do not revive terminal work or accept a superseded generation."""
    # Make an active, stored issue eligible for participant attach.
    current = state()
    current["disposition"] = "completed"
    current["storage"] = "present"
    before = copy.deepcopy(current)
    # Attempt attach without the required assignment or valid binding.
    with pytest.raises(core.WorkspaceError) as captured:
        core.attach(current, request())  # type: ignore[arg-type]
    # Confirm the rejected operation reports RECOVERY_REQUIRED.
    assert captured.value.code == "RECOVERY_REQUIRED"
    assert current == before
    # Mark the issue terminal before attempting a new attach.
    current["disposition"] = "active"
    # Attempt attach without the required assignment or valid binding.
    with pytest.raises(core.WorkspaceError) as captured:
        core.attach(current, {**request(), "binding_generation": 1})  # type: ignore[arg-type]
    # Confirm the rejected operation reports STALE_BINDING.
    assert captured.value.code == "STALE_BINDING"
