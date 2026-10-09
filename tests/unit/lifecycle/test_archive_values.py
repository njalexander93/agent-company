"""Check archive values, document framing, and digest-chain continuity."""

from __future__ import annotations

import base64

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def event_line(seq: int, prior: str | None, code: str) -> tuple[bytes, str]:
    """Build one independently digestible event record for chain tests.

    Args:
        seq: Sequence number written into the event.
        prior: Previous event digest, or None for the first event.
        code: Error or event code expected in this case.

    Returns:
        Serialized event line and its digest for the next chain link.
    """
    # Assemble the event fields before hashing their canonical bytes.
    payload = {"seq": seq, "prev_digest": prior, "code": code}
    # Hash the canonical event fields to link the digest chain.
    digest = core.sha(core.canonical(payload))
    return core.canonical({**payload, "digest": digest}) + b"\n", digest


def test_participant_key_uses_explicit_host_and_session() -> None:
    """Distinguish identical session names on different hosts."""
    # Derive participant keys from explicit host and session pairs.
    first = core.participant_key({"host": "codex", "session_id": "same"})
    # Confirm same inputs are stable and different hosts produce distinct keys.
    assert first == core.participant_key({"host": "codex", "session_id": "same"})
    assert first != core.participant_key({"host": "cursor", "session_id": "same"})
    assert len(first) == 64


@pytest.mark.parametrize(
    ("name", "expected"),
    [(".DS_Store", True), ("._note.md", True), ("._", False), ("note.md", False)],
)
def test_os_metadata_has_narrow_name_policy(name: str, expected: bool) -> None:
    """Exclude only Finder metadata spellings.

    Args:
        name: Requested file, directory, or control-entry name.
        expected: Expected result for this input.
    """
    assert core.os_metadata(name) is expected


def test_manifest_sorts_paths_and_hashes_exact_bytes() -> None:
    """Retain path order and byte identity in the manifest."""
    # Supply file bytes and paths in deliberately unsorted order.
    files = {"roadmap.md": b"later", "context/a.md": b"earlier"}
    # Build the canonical manifest from those exact bytes.
    result = core.manifest(files)
    # Confirm path ordering and byte-specific digest values.
    assert list(result) == ["context/a.md", "roadmap.md"]
    assert result["context/a.md"] == core.sha(b"earlier")
    assert result["roadmap.md"] != core.sha(b"earlier")


def test_validate_history_joins_retained_segment_and_active_stream() -> None:
    """Carry the exact digest anchor across a segment boundary."""
    # Supply file bytes and paths in deliberately unsorted order.
    first, first_digest = event_line(1, None, "START")
    second, second_digest = event_line(2, first_digest, "END")
    files = {
        "events-000000000001-000000000001.jsonl": first,
        "events.jsonl": second,
    }
    # Confirm retained and active events form one validated chain.
    assert core.validate_history(files) == (2, second_digest)
    assert core.validate_history({}) == (0, None)


def test_validate_history_rejects_segment_range_mismatch() -> None:
    """Reject a segment name whose declared last sequence exceeds its bytes."""
    # Build one event whose segment name falsely claims two records.
    first, _ = event_line(1, None, "START")
    # Reject the inconsistent segment range during validation.
    with pytest.raises(core.WorkspaceError) as captured:
        core.validate_history({"events-000000000001-000000000002.jsonl": first})
    # Confirm the rejected operation reports INTEGRITY_ERROR.
    assert captured.value.code == "INTEGRITY_ERROR"


def test_validate_events_rejects_oversized_record() -> None:
    """Reject a complete event line beyond its independent line budget."""
    # Reject an event line beyond the independent size limit.
    with pytest.raises(core.WorkspaceError) as captured:
        core.validate_events(b"x" * 8193 + b"\n")
    # Confirm the rejected operation reports INTEGRITY_ERROR.
    assert captured.value.code == "INTEGRITY_ERROR"


def snapshot_state() -> dict[str, object]:
    """Supply a minimal committed state for archive serialization.

    Returns:
        Minimal committed state for archive serialization.
    """
    return {
        "repo_id": "repo",
        "issue_id": "AGENT-30",
        "issue_uuid": "uuid",
        "revision": 3,
        "disposition": "active",
        "seq": 0,
        "head": None,
    }


def test_archive_payload_retains_sorted_reconstructable_bytes() -> None:
    """Freeze exact file bytes and their digest without mutating the state."""
    # Build the committed state or provider observations for this case.
    state = snapshot_state()
    files = {"roadmap.md": b"roadmap", "context/a.md": b"\x00\xff"}
    # Freeze the committed state and file bytes for export.
    result = core.archive_payload(state, files)  # type: ignore[arg-type]
    # Inspect the sorted file entries and their encoded bytes.
    entries = result["files"]
    assert [item["path"] for item in entries] == ["context/a.md", "roadmap.md"]
    assert [base64.b64decode(item["data"]) for item in entries] == [b"\x00\xff", b"roadmap"]
    assert [item["size"] for item in entries] == [2, 7]
    assert state == snapshot_state()


def test_archive_payload_preserves_segment_lineage_when_present() -> None:
    """Retain rollover metadata in the frozen snapshot."""
    # Build the committed state or provider observations for this case.
    state = {**snapshot_state(), "event_segments": [{"first": 1, "last": 2}]}
    # Freeze the committed state and file bytes for export.
    result = core.archive_payload(state, {})  # type: ignore[arg-type]
    assert result["event_segments"] == [{"first": 1, "last": 2}]


def test_document_roundtrip_has_one_canonical_json_block() -> None:
    """Frame readable text around one exact JSON value."""
    # Encode a canonical JSON value inside the readable document frame.
    content = core.document({"b": 2, "a": "é"}, "# Archive")
    # Confirm the frame has one parseable JSON block.
    assert content.startswith("# Archive\n\n```json\n")
    assert content.endswith("\n```\n")
    assert core.parse_document(content) == {"a": "é", "b": 2}


def test_export_documents_keeps_reconstructable_snapshot_and_readable_history() -> None:
    """Save complete bytes while presenting the note text to a reviewer."""
    # Freeze the committed state and file bytes for export.
    snapshot = core.archive_payload(snapshot_state(), {"roadmap.md": b"Approved work"})  # type: ignore[arg-type]
    exported = core.export_documents(snapshot)
    # Confirm the exported snapshot digest and part count.
    assert exported["snapshot"] == core.sha(core.canonical(snapshot))
    assert len(exported["parts"]) == 1
    # Inspect the exported part and its embedded snapshot.
    part = exported["parts"][0]
    assert part["issue"] == "uuid"
    assert "Approved work" in part["content"]
    # Parse the exported document to recover the exact frozen bytes.
    parsed = core.parse_document(part["content"])
    assert parsed["snapshot"] == exported["snapshot"]
    assert base64.b64decode(parsed["data"]) == core.canonical(snapshot)


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("no block", "INTEGRITY_ERROR"),
        ("```json\n{}\n```\n```json\n{}\n```", "INTEGRITY_ERROR"),
        ("```json\n{\n```", "INVALID_REQUEST"),
    ],
)
def test_parse_document_rejects_missing_duplicate_or_invalid_block(content: str, code: str) -> None:
    """Require exactly one valid structured archive value.

    Args:
        content: Document text supplied to the parser.
        code: Error or event code expected in this case.
    """
    # Reject missing, duplicate, or malformed JSON frames.
    with pytest.raises(core.WorkspaceError) as captured:
        core.parse_document(content)
    # Match the failure code to the malformed input case.
    assert captured.value.code == code


def test_provider_observation_requires_verified_parent_and_origin() -> None:
    """Admit a matching document read and reject a different parent."""
    # Build a provider observation tied to the expected issue and origin.
    observed = {
        "id": "document-id",
        "url": "https://linear.app/team/document/id",
        "issue": "uuid",
        "updatedAt": "2026-10-08T00:00:00Z",
        "content": core.document({"kind": "part"}, "# Archive"),
        "origin": "linear_get_document",
        "request_id": "request-id",
    }
    # Accept the matching observation before changing its parent issue.
    assert core.provider_observation(observed, "uuid") == {"kind": "part"}
    # Reject the observation after its parent issue changes.
    with pytest.raises(core.WorkspaceError) as captured:
        core.provider_observation({**observed, "issue": "other"}, "uuid")
    # Confirm the rejected operation reports ARCHIVE_PENDING.
    assert captured.value.code == "ARCHIVE_PENDING"


def observations_for(
    snapshot: dict[str, object],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Build a provider read-back set from a complete local frozen snapshot.

    Args:
        snapshot: Frozen archive payload being checked.

    Returns:
        Provider observations and descriptors for every exported part.
    """
    # Export a part for each frozen file before constructing read-backs.
    exported = core.export_documents(snapshot)
    # Collect the provider observations and corresponding part descriptors.
    observed: list[dict[str, object]] = []
    descriptors: list[dict[str, str]] = []
    # Give each exported part a stable provider ID, version, and digest.
    for index, part in enumerate(exported["parts"]):
        # Assign the part its read-back identity and version.
        document_id = f"part-{index}"
        version = f"2026-10-08T00:00:{index:02d}Z"
        # Parse the exported document to recover the exact frozen bytes.
        parsed = core.parse_document(part["content"])
        observed.append(
            {
                "id": document_id,
                "url": f"https://linear.app/team/document/{document_id}",
                "issue": "uuid",
                "updatedAt": version,
                "content": part["content"],
                "origin": "linear_get_document",
                "request_id": "readback",
            }
        )
        descriptors.append(
            {"id": document_id, "updatedAt": version, "sha256": core.sha(core.canonical(parsed))}
        )
    # Encode a canonical JSON value inside the readable document frame.
    index_content = core.document(
        {
            "schema_version": 1,
            "kind": "index",
            "snapshot": exported["snapshot"],
            "parts": descriptors,
        },
        "# Archive index",
    )
    observed.append(
        {
            "id": "root",
            "url": "https://linear.app/team/document/root",
            "issue": "uuid",
            "updatedAt": "2026-10-08T00:01:00Z",
            "content": index_content,
            "origin": "linear_get_document",
            "request_id": "readback",
        }
    )
    return {**snapshot_state(), "export": {"snapshot": exported["snapshot"]}}, observed


def test_verify_provider_reconstructs_exact_bytes_and_receipt() -> None:
    """Check independently observed parts against the frozen snapshot identity."""
    # Freeze the committed state and file bytes for export.
    snapshot = core.archive_payload(
        snapshot_state(), {"roadmap.md": b"Approved", "events.jsonl": b""}
    )  # type: ignore[arg-type]
    # Build the committed state or provider observations for this case.
    state, observations = observations_for(snapshot)
    # Reconstruct the snapshot from independently observed documents.
    restored, files, receipt = core.verify_provider(state, observations)  # type: ignore[arg-type]
    assert restored == snapshot
    assert files == {"roadmap.md": b"Approved", "events.jsonl": b""}
    assert receipt["root"]["id"] == "root"
    assert receipt["snapshot"] == core.sha(core.canonical(snapshot))
    assert receipt["observations_digest"] == core.sha(core.canonical(observations))


def test_verify_provider_rejects_wrong_document_version() -> None:
    """Reject a part whose observed version differs from the root index."""
    # Freeze the committed state and file bytes for export.
    snapshot = core.archive_payload(
        snapshot_state(), {"roadmap.md": b"Approved", "events.jsonl": b""}
    )  # type: ignore[arg-type]
    # Build the committed state or provider observations for this case.
    state, observations = observations_for(snapshot)
    observations[0] = {**observations[0], "updatedAt": "different-version"}
    # Reject the read-back when archive identity or required files disagree.
    with pytest.raises(core.WorkspaceError) as captured:
        core.verify_provider(state, observations)  # type: ignore[arg-type]
    # Confirm the rejected operation reports ARCHIVE_PENDING.
    assert captured.value.code == "ARCHIVE_PENDING"


def test_verify_provider_rejects_snapshot_without_required_roadmap() -> None:
    """Refuse a reconstructable archive that omits the canonical roadmap."""
    # Freeze the committed state and file bytes for export.
    snapshot = core.archive_payload(snapshot_state(), {"events.jsonl": b""})  # type: ignore[arg-type]
    # Build the committed state or provider observations for this case.
    state, observations = observations_for(snapshot)
    # Reject the read-back when archive identity or required files disagree.
    with pytest.raises(core.WorkspaceError) as captured:
        core.verify_provider(state, observations)  # type: ignore[arg-type]
    # Confirm the rejected operation reports INTEGRITY_ERROR.
    assert captured.value.code == "INTEGRITY_ERROR"


def test_eligible_requires_terminal_outcome_detached_workers_and_archive() -> None:
    """Reject cleanup until every retention condition is satisfied."""
    # Build the committed state or provider observations for this case.
    state = {
        "disposition": "completed",
        "outcome": {"code": "DONE"},
        "participants": {"worker": {"status": "detached", "pending": {}}},
        "export": {"snapshot": "digest"},
        "archive": {"root": "id"},
    }
    # Confirm completed, detached, archived work is eligible for cleanup.
    assert core.eligible(state) is None  # type: ignore[arg-type]
    # Remove one required retention condition at a time.
    for change, code in (
        ({"disposition": "active"}, "RETAINED"),
        ({"outcome": None}, "RETAINED"),
        ({"participants": {"worker": {"status": "ready", "pending": {}}}}, "RETAINED"),
        ({"participants": {"worker": {"status": "detached", "pending": {"tool": {}}}}}, "RETAINED"),
        ({"export": None}, "ARCHIVE_PENDING"),
        ({"archive": None}, "ARCHIVE_PENDING"),
    ):
        # Reject cleanup when that condition is absent.
        with pytest.raises(core.WorkspaceError) as captured:
            core.eligible({**state, **change})  # type: ignore[arg-type]
        # Match the rejection code to the missing retention condition.
        assert captured.value.code == code


def test_execute_rejects_malformed_request_without_store_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return bounded failures before opening a persistent store.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Fail the test if malformed input reaches persistent store access.
    monkeypatch.setattr(core, "Store", lambda _request: pytest.fail("store opened"))
    # Dispatch the malformed public request through validation.
    result = core.execute({"schema_version": 1, "request_id": "id", "operation": "read"})
    # Confirm the rejected operation reports INVALID_REQUEST.
    assert result["ok"] is False
    assert result["code"] == "INVALID_REQUEST"
    assert "issue_id" not in result


def test_execute_register_dispatch_preserves_public_failure_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route register exactly once without requiring an existing repo identity.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Record registration calls to detect duplicate or forbidden dispatch.
    calls: list[dict[str, object]] = []

    def register(request: dict[str, object]) -> dict[str, object]:
        """Record the registration request for this isolated dispatch test.

        Args:
            request: Lifecycle request sent to the operation.

        Returns:
            Lifecycle registration result for the test workspace.
        """
        # Capture the exact request passed into registration.
        calls.append(request)
        return {"ok": False, "code": "REPOSITORY_MISMATCH"}

    # Replace registration with a bounded failure response.
    monkeypatch.setattr(core, "register", register)
    request = {"schema_version": 1, "request_id": "id", "operation": "register"}
    # Preserve the registration failure and reject an invented repository ID.
    assert core.execute(request) == {"ok": False, "code": "REPOSITORY_MISMATCH"}
    assert calls == [request]
    assert core.execute({**request, "repo_id": "unexpected"})["code"] == "INVALID_REQUEST"
    assert calls == [request]
