"""Assert archive provider admission against one immutable local request."""

from __future__ import annotations

from typing import Any

import pytest

from agent_company.adapters import codex
from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


class Control:
    """Expose only the previously frozen export document."""

    def __enter__(self) -> Control:
        """Open the modeled issue control handle."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the modeled issue control handle."""

    def lock(self) -> Control:
        """Hold the modeled issue lock."""
        return self

    def json(self, name: str) -> dict[str, object]:
        """Return the frozen export by its exact snapshot name."""
        assert name == "export-snapshot.json"
        return {"parts": [{"issue": "uuid", "title": "part", "content": "frozen content"}]}


class Issues:
    """Expose one selected issue control directory."""

    def child(self, identifier: str) -> Control:
        """Require the bound issue identity."""
        assert identifier == "AGENT-30"
        return Control()


class Store:
    """Expose a selected issue without native storage operations."""

    def __init__(self, _request: dict[str, object]) -> None:
        """Set the issue directory adapter."""
        self.issues = Issues()

    def __enter__(self) -> Store:
        """Open the modeled store."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the modeled store."""


class Issue:
    """Expose one committed coordinator state to the provider gate."""

    def __init__(self, _store: Store, _control: Control, _identifier: str) -> None:
        """Prepare one state with a frozen export and read-back identity."""
        key = core.participant_key({"host": "codex", "session_id": "session"})
        self.state: dict[str, Any] = {
            "storage": "present",
            "coordinator": key,
            "participants": {key: {"generation": 1, "status": "ready"}},
            "provider_saves": {"digest": {"id": "part-1"}},
            "export": {"snapshot": "snapshot"},
        }
        self.files_read = False

    def recover(self) -> None:
        """Model successful transaction recovery."""

    def committed_state(self) -> dict[str, Any]:
        """Return the modeled committed state."""
        return self.state

    def files(self) -> dict[str, bytes]:
        """Record that manifest validation preceded provider admission."""
        self.files_read = True
        return {"roadmap.md": b"approved"}


def install_gate(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Install only the bounded store and lifecycle operation boundaries."""
    monkeypatch.setattr(
        codex,
        "request_for",
        lambda _event, operation: {
            "operation": operation,
            "host": "codex",
            "session_id": "session",
            "issue_id": "AGENT-30",
            "binding_generation": 1,
        },
    )
    monkeypatch.setattr(codex.core, "Store", Store)
    monkeypatch.setattr(codex.core, "Issue", Issue)
    calls: list[dict[str, object]] = []

    def execute(request: dict[str, object]) -> dict[str, object]:
        """Capture the durable uncertainty operation before provider write."""
        calls.append(request)
        return {"ok": True, "code": "OK"}

    monkeypatch.setattr(codex.core, "execute", execute)
    return calls


def test_provider_gate_allows_only_recorded_document_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep unrecorded document IDs outside the archive scope."""
    calls = install_gate(monkeypatch)
    base = {"tool_name": "mcp__codex_apps__linear_get_document"}
    assert codex.provider_gate({**base, "tool_input": {"id": "part-1"}}) is True
    assert codex.provider_gate({**base, "tool_input": {"id": "other"}}) is False
    assert codex.provider_gate({**base, "tool_input": {"id": "part-1", "extra": 1}}) is False
    assert calls == []


def test_provider_gate_records_save_uncertainty_for_exact_frozen_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow the provider write only after archive-save-start succeeds."""
    calls = install_gate(monkeypatch)
    base = {"tool_name": "mcp__codex_apps__linear_save_document"}
    exact = {"issue": "uuid", "title": "part", "content": "frozen content"}
    assert codex.provider_gate({**base, "tool_input": {**exact, "content": "changed"}}) is False
    assert calls == []
    assert codex.provider_gate({**base, "tool_input": exact}) is True
    assert len(calls) == 1
    assert calls[0]["operation"] == "archive-save-start"
    assert calls[0]["content_digest"] == core.sha(b"frozen content")


def test_provider_gate_ignores_unrelated_tools_before_store_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only archive document transport enters the durable provider gate."""
    monkeypatch.setattr(codex, "request_for", lambda *_args: pytest.fail("binding lookup"))
    assert codex.provider_gate({"tool_name": "mcp__codex_apps__linear_get_issue"}) is False


def test_provider_gate_reads_recorded_archive_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleaned issue can read immutable root and part documents, never save anew."""
    calls = install_gate(monkeypatch)
    state = Issue(None, None, "AGENT-30").state  # type: ignore[arg-type]
    state.update(
        storage="cleaned",
        archive={"root": {"id": "root-1"}, "parts": [{"id": "archive-part-1"}]},
    )
    monkeypatch.setattr(Issue, "committed_state", lambda _self: state)
    monkeypatch.setattr(Issue, "files", lambda _self: pytest.fail("cleaned files read"))
    for identifier in ("root-1", "archive-part-1", "part-1"):
        assert (
            codex.provider_gate(
                {
                    "tool_name": "mcp__codex_apps__linear_get_document",
                    "tool_input": {"id": identifier},
                }
            )
            is True
        )
    assert (
        codex.provider_gate(
            {"tool_name": "mcp__codex_apps__linear_save_document", "tool_input": {"content": "x"}}
        )
        is False
    )
    assert calls == []


def test_provider_gate_rejects_save_when_uncertainty_record_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed archive-save-start must deny the external provider write."""
    calls = install_gate(monkeypatch)

    def reject(request: dict[str, object]) -> dict[str, object]:
        """Record the attempted uncertainty write and reject it."""
        calls.append(request)
        return {"ok": False, "code": "REVISION_CONFLICT"}

    monkeypatch.setattr(codex.core, "execute", reject)
    exact = {"issue": "uuid", "title": "part", "content": "frozen content"}
    assert (
        codex.provider_gate(
            {"tool_name": "mcp__codex_apps__linear_save_document", "tool_input": exact}
        )
        is False
    )
    assert len(calls) == 1
    assert calls[0]["operation"] == "archive-save-start"


def test_provider_gate_allows_only_frozen_index_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The optional index is admitted only as the exact exported request."""
    calls = install_gate(monkeypatch)
    state = Issue(None, None, "AGENT-30").state  # type: ignore[arg-type]
    index = {"issue": "uuid", "title": "index", "content": "frozen index"}
    state["index_request"] = index
    monkeypatch.setattr(Issue, "committed_state", lambda _self: state)
    assert (
        codex.provider_gate(
            {"tool_name": "mcp__codex_apps__linear_save_document", "tool_input": index}
        )
        is True
    )
    assert len(calls) == 1
    assert calls[0]["content_digest"] == core.sha(b"frozen index")
