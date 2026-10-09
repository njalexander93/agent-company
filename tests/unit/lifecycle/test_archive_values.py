"""Check archive values, document framing, and digest-chain continuity."""

from __future__ import annotations

import base64

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def event_line(seq: int, prior: str | None, code: str) -> tuple[bytes, str]:
    """Build one independently digestible event record for chain tests."""
    payload = {"seq": seq, "prev_digest": prior, "code": code}
    digest = core.sha(core.canonical(payload))
    return core.canonical({**payload, "digest": digest}) + b"\n", digest


def test_participant_key_uses_explicit_host_and_session() -> None:
    """Distinguish identical session names on different hosts."""
    first = core.participant_key({"host": "codex", "session_id": "same"})
    assert first == core.participant_key({"host": "codex", "session_id": "same"})
    assert first != core.participant_key({"host": "cursor", "session_id": "same"})
    assert len(first) == 64


@pytest.mark.parametrize(
    ("name", "expected"),
    [(".DS_Store", True), ("._note.md", True), ("._", False), ("note.md", False)],
)
def test_os_metadata_has_narrow_name_policy(name: str, expected: bool) -> None:
    """Exclude only Finder metadata spellings."""
    assert core.os_metadata(name) is expected


def test_manifest_sorts_paths_and_hashes_exact_bytes() -> None:
    """Retain path order and byte identity in the manifest."""
    files = {"roadmap.md": b"later", "context/a.md": b"earlier"}
    result = core.manifest(files)
    assert list(result) == ["context/a.md", "roadmap.md"]
    assert result["context/a.md"] == core.sha(b"earlier")
    assert result["roadmap.md"] != core.sha(b"earlier")


def test_validate_history_joins_retained_segment_and_active_stream() -> None:
    """Carry the exact digest anchor across a segment boundary."""
    first, first_digest = event_line(1, None, "START")
    second, second_digest = event_line(2, first_digest, "END")
    files = {
        "events-000000000001-000000000001.jsonl": first,
        "events.jsonl": second,
    }
    assert core.validate_history(files) == (2, second_digest)
    assert core.validate_history({}) == (0, None)


def test_validate_history_rejects_segment_range_mismatch() -> None:
    """Reject a segment name whose declared last sequence exceeds its bytes."""
    first, _ = event_line(1, None, "START")
    with pytest.raises(core.WorkspaceError) as captured:
        core.validate_history({"events-000000000001-000000000002.jsonl": first})
    assert captured.value.code == "INTEGRITY_ERROR"


def test_validate_events_rejects_oversized_record() -> None:
    """Reject a complete event line beyond its independent line budget."""
    with pytest.raises(core.WorkspaceError) as captured:
        core.validate_events(b"x" * 8193 + b"\n")
    assert captured.value.code == "INTEGRITY_ERROR"


def snapshot_state() -> dict[str, object]:
    """Supply a minimal committed state for archive serialization."""
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
    state = snapshot_state()
    files = {"roadmap.md": b"roadmap", "context/a.md": b"\x00\xff"}
    result = core.archive_payload(state, files)  # type: ignore[arg-type]
    entries = result["files"]
    assert [item["path"] for item in entries] == ["context/a.md", "roadmap.md"]
    assert [base64.b64decode(item["data"]) for item in entries] == [b"\x00\xff", b"roadmap"]
    assert [item["size"] for item in entries] == [2, 7]
    assert state == snapshot_state()


def test_archive_payload_preserves_segment_lineage_when_present() -> None:
    """Retain rollover metadata in the frozen snapshot."""
    state = {**snapshot_state(), "event_segments": [{"first": 1, "last": 2}]}
    result = core.archive_payload(state, {})  # type: ignore[arg-type]
    assert result["event_segments"] == [{"first": 1, "last": 2}]


def test_document_roundtrip_has_one_canonical_json_block() -> None:
    """Frame readable text around one exact JSON value."""
    content = core.document({"b": 2, "a": "é"}, "# Archive")
    assert content.startswith("# Archive\n\n```json\n")
    assert content.endswith("\n```\n")
    assert core.parse_document(content) == {"a": "é", "b": 2}


def test_export_documents_keeps_reconstructable_snapshot_and_readable_history() -> None:
    """Save complete bytes while presenting the note text to a reviewer."""
    snapshot = core.archive_payload(snapshot_state(), {"roadmap.md": b"Approved work"})  # type: ignore[arg-type]
    exported = core.export_documents(snapshot)
    assert exported["snapshot"] == core.sha(core.canonical(snapshot))
    assert len(exported["parts"]) == 1
    part = exported["parts"][0]
    assert part["issue"] == "uuid"
    assert "Approved work" in part["content"]
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
    """Require exactly one valid structured archive value."""
    with pytest.raises(core.WorkspaceError) as captured:
        core.parse_document(content)
    assert captured.value.code == code


def test_provider_observation_requires_verified_parent_and_origin() -> None:
    """Admit a matching document read and reject a different parent."""
    observed = {
        "id": "document-id",
        "url": "https://linear.app/team/document/id",
        "issue": "uuid",
        "updatedAt": "2026-10-08T00:00:00Z",
        "content": core.document({"kind": "part"}, "# Archive"),
        "origin": "linear_get_document",
        "request_id": "request-id",
    }
    assert core.provider_observation(observed, "uuid") == {"kind": "part"}
    with pytest.raises(core.WorkspaceError) as captured:
        core.provider_observation({**observed, "issue": "other"}, "uuid")
    assert captured.value.code == "ARCHIVE_PENDING"


def observations_for(
    snapshot: dict[str, object],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Build a provider read-back set from a complete local frozen snapshot."""
    exported = core.export_documents(snapshot)
    observed: list[dict[str, object]] = []
    descriptors: list[dict[str, str]] = []
    for index, part in enumerate(exported["parts"]):
        document_id = f"part-{index}"
        version = f"2026-10-08T00:00:{index:02d}Z"
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
    snapshot = core.archive_payload(
        snapshot_state(), {"roadmap.md": b"Approved", "events.jsonl": b""}
    )  # type: ignore[arg-type]
    state, observations = observations_for(snapshot)
    restored, files, receipt = core.verify_provider(state, observations)  # type: ignore[arg-type]
    assert restored == snapshot
    assert files == {"roadmap.md": b"Approved", "events.jsonl": b""}
    assert receipt["root"]["id"] == "root"
    assert receipt["snapshot"] == core.sha(core.canonical(snapshot))
    assert receipt["observations_digest"] == core.sha(core.canonical(observations))


def test_verify_provider_rejects_wrong_document_version() -> None:
    """Reject a part whose observed version differs from the root index."""
    snapshot = core.archive_payload(
        snapshot_state(), {"roadmap.md": b"Approved", "events.jsonl": b""}
    )  # type: ignore[arg-type]
    state, observations = observations_for(snapshot)
    observations[0] = {**observations[0], "updatedAt": "different-version"}
    with pytest.raises(core.WorkspaceError) as captured:
        core.verify_provider(state, observations)  # type: ignore[arg-type]
    assert captured.value.code == "ARCHIVE_PENDING"


def test_verify_provider_rejects_snapshot_without_required_roadmap() -> None:
    """Refuse a reconstructable archive that omits the canonical roadmap."""
    snapshot = core.archive_payload(snapshot_state(), {"events.jsonl": b""})  # type: ignore[arg-type]
    state, observations = observations_for(snapshot)
    with pytest.raises(core.WorkspaceError) as captured:
        core.verify_provider(state, observations)  # type: ignore[arg-type]
    assert captured.value.code == "INTEGRITY_ERROR"


def test_eligible_requires_terminal_outcome_detached_workers_and_archive() -> None:
    """Reject cleanup until every retention condition is satisfied."""
    state = {
        "disposition": "completed",
        "outcome": {"code": "DONE"},
        "participants": {"worker": {"status": "detached", "pending": {}}},
        "export": {"snapshot": "digest"},
        "archive": {"root": "id"},
    }
    assert core.eligible(state) is None  # type: ignore[arg-type]
    for change, code in (
        ({"disposition": "active"}, "RETAINED"),
        ({"outcome": None}, "RETAINED"),
        ({"participants": {"worker": {"status": "ready", "pending": {}}}}, "RETAINED"),
        ({"participants": {"worker": {"status": "detached", "pending": {"tool": {}}}}}, "RETAINED"),
        ({"export": None}, "ARCHIVE_PENDING"),
        ({"archive": None}, "ARCHIVE_PENDING"),
    ):
        with pytest.raises(core.WorkspaceError) as captured:
            core.eligible({**state, **change})  # type: ignore[arg-type]
        assert captured.value.code == code


def test_execute_rejects_malformed_request_without_store_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return bounded failures before opening a persistent store."""
    monkeypatch.setattr(core, "Store", lambda _request: pytest.fail("store opened"))
    result = core.execute({"schema_version": 1, "request_id": "id", "operation": "read"})
    assert result["ok"] is False
    assert result["code"] == "INVALID_REQUEST"
    assert "issue_id" not in result


def test_execute_register_dispatch_preserves_public_failure_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route register exactly once without requiring an existing repo identity."""
    calls: list[dict[str, object]] = []

    def register(request: dict[str, object]) -> dict[str, object]:
        """Record the registration request for this isolated dispatch test."""
        calls.append(request)
        return {"ok": False, "code": "REPOSITORY_MISMATCH"}

    monkeypatch.setattr(core, "register", register)
    request = {"schema_version": 1, "request_id": "id", "operation": "register"}
    assert core.execute(request) == {"ok": False, "code": "REPOSITORY_MISMATCH"}
    assert calls == [request]
    assert core.execute({**request, "repo_id": "unexpected"})["code"] == "INVALID_REQUEST"
    assert calls == [request]
