"""Check lifecycle command parsing and bounded diagnostic translation."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def test_fs_identity_returns_validated_native_descriptor_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Read only the identity returned by an owned absolute directory handle."""
    paths: list[str | Path] = []

    class Handle:
        """Expose one modeled owned directory identity."""

        identity = [7, 11]

        def __enter__(self) -> Handle:
            """Return the opened native directory."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled directory."""

    monkeypatch.setattr(core.Directory, "absolute", lambda path: paths.append(path) or Handle())
    assert core.fs_identity(tmp_path / "main") == [7, 11]
    assert paths == [tmp_path / "main"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (core.WorkspaceError("BINDING_MISSING"), "BINDING_MISSING"),
        (OSError("private detail"), "RECOVERY_REQUIRED"),
        (KeyError("private detail"), "INVALID_REQUEST"),
    ],
)
def test_execute_returns_bounded_diagnostics_without_exception_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception, code: str
) -> None:
    """Keep unexpected exception contents out of the public lifecycle response."""
    monkeypatch.setattr(core, "repository", lambda _root: (_ for _ in ()).throw(error))
    response = core.execute(
        {
            "schema_version": 1,
            "request_id": "r",
            "operation": "diagnose",
            "worktree": str(tmp_path / "main"),
        }
    )
    assert response["ok"] is False
    assert response["code"] == code
    assert "private detail" not in str(response)


def test_execute_permission_response_lists_only_derived_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Give a narrow retry path when opening the repository is permission denied."""
    monkeypatch.setattr(
        core, "repository", lambda _root: (_ for _ in ()).throw(PermissionError("secret"))
    )
    bindings = str(tmp_path / "main/.task/.bindings")
    monkeypatch.setattr(core, "permission_paths", lambda _request: [bindings])
    response = core.execute(
        {
            "schema_version": 1,
            "request_id": "r",
            "operation": "diagnose",
            "worktree": str(tmp_path / "main"),
        }
    )
    assert response["code"] == "PERMISSION_REQUIRED"
    assert response["paths"] == [bindings]
    assert "secret" not in str(response)


def test_main_reads_one_argument_and_emits_canonical_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Accept one bounded JSON argument and preserve the command failure status."""
    seen: list[dict[str, Any]] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Capture parsed request and return a public diagnostic."""
        seen.append(request)
        return {"ok": False, "code": "BINDING_MISSING"}

    monkeypatch.setattr(core, "execute", execute)
    monkeypatch.setattr(
        sys, "argv", ["task_workspace.py", "--request-json", '{"operation":"read"}']
    )
    assert core.main() == 3
    assert seen == [{"operation": "read"}]
    assert capsys.readouterr().out == '{"code":"BINDING_MISSING","ok":false}\n'


def test_main_rejects_oversized_stdin_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stop before core dispatch when standard input exceeds the CLI bound."""

    class Input:
        """Expose bounded binary standard input for CLI parsing."""

        buffer = StringIO()

    class Buffer:
        """Record the maximum byte count requested from standard input."""

        def read(self, limit: int) -> bytes:
            """Return one byte beyond the documented request size."""
            assert limit == core.MAX_REQUEST + 1
            return b"x" * limit

    input_stream = Input()
    input_stream.buffer = Buffer()  # type: ignore[assignment]
    monkeypatch.setattr(sys, "stdin", input_stream)
    monkeypatch.setattr(sys, "argv", ["task_workspace.py"])
    monkeypatch.setattr(core, "execute", lambda _request: pytest.fail("dispatched oversized input"))
    assert core.main() == 3
    assert capsys.readouterr().out == '{"code":"SIZE_LIMIT","ok":false}\n'


def test_execute_routes_registration_without_supplied_repo_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Call registration only for a schema-valid request without claimed repository ID."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        core,
        "register",
        lambda request: calls.append(request) or {"ok": True, "code": "REGISTERED"},
    )
    request = {"schema_version": 1, "request_id": "r", "operation": "register"}
    assert core.execute(request) == {"ok": True, "code": "REGISTERED"}
    assert calls == [request]
    response = core.execute({**request, "repo_id": "claimed"})
    assert response["code"] == "INVALID_REQUEST"
    assert calls == [request]


def test_execute_discovers_only_recorded_registration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Report registration required until the direct local record exists."""
    present: set[str] = set()
    root = tmp_path / "checkout"
    monkeypatch.setattr(core, "repository", lambda _path: (root, None, [root]))

    class Directory:
        """Expose root and task direct entries for registration discovery."""

        def __init__(self, level: int = 0) -> None:
            """Select root or task directory depth."""
            self.level = level

        def __enter__(self) -> Directory:
            """Hold the modeled directory handle."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled directory handle."""

        def exists(self, name: str) -> bool:
            """Check one direct entry at the selected level."""
            return name in present

        def child(self, name: str) -> Directory:
            """Open only the direct task directory."""
            assert name == ".task"
            return Directory(1)

        def json(self, name: str) -> dict[str, str]:
            """Read the exact local repository registration."""
            assert (self.level, name) == (1, ".repository.json")
            return {"repo_id": "repo"}

    monkeypatch.setattr(core.Directory, "absolute", lambda _path: Directory())
    request = {
        "schema_version": 1,
        "request_id": "r",
        "operation": "diagnose",
        "worktree": str(root),
    }
    assert core.execute(request)["code"] == "REGISTRATION_REQUIRED"
    present.add(".task")
    assert core.execute(request)["code"] == "REGISTRATION_REQUIRED"
    present.add(".repository.json")
    assert core.execute(request) == {"ok": True, "code": "REGISTERED", "repo_id": "repo"}


def test_execute_serializes_issue_dispatch_and_explicit_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Hold binding and issue locks before a create and collect only supplied candidates."""
    calls: list[str] = []

    class Lock:
        """Record one held binding or issue lock."""

        def __init__(self, name: str) -> None:
            """Name the held lock."""
            self.name = name

        def __enter__(self) -> Lock:
            """Mark lock acquisition before lifecycle dispatch."""
            calls.append("enter:" + self.name)
            return self

        def __exit__(self, *_args: object) -> None:
            """Mark lock release after lifecycle dispatch."""
            calls.append("exit:" + self.name)

    class Node:
        """Expose direct child and lock operations only."""

        def __enter__(self) -> Node:
            """Hold a selected issue control handle."""
            calls.append("enter:control")
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the selected issue control handle."""
            calls.append("exit:control")

        def lock(self, name: str = "issue.lock") -> Lock:
            """Return the selected persistent lock."""
            return Lock(name)

        def child(self, identifier: str, create: bool = False) -> Node:
            """Open only the selected issue's direct control directory."""
            assert (identifier, create) == ("AGENT-30", True)
            return self

    class Store:
        """Expose bounded binding and issue control handles."""

        def __init__(self, _request: dict[str, Any]) -> None:
            """Construct the selected registered store."""
            self.bindings = Node()
            self.issues = Node()

        def __enter__(self) -> Store:
            """Hold the store for issue dispatch."""
            calls.append("enter:store")
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the store after collection."""
            calls.append("exit:store")

    monkeypatch.setattr(core, "Store", Store)
    monkeypatch.setattr(core, "Issue", lambda _store, _control, identifier: identifier)
    monkeypatch.setattr(
        core,
        "operate",
        lambda _store, issue, _request: (
            calls.append("operate:" + issue) or {"ok": True, "code": "CREATED"}
        ),
    )
    monkeypatch.setattr(
        core,
        "collect_candidates",
        lambda _store, _request: calls.append("collect") or [{"issue_id": "AGENT-29"}],
    )
    request = {
        "schema_version": 1,
        "request_id": "r",
        "operation": "create",
        "worktree": str(tmp_path / "checkout"),
        "repo_id": "repo",
        "issue_id": "AGENT-30",
        "host": "codex",
        "session_id": "session",
        "cleanup_candidates": [{"issue_id": "AGENT-29"}],
    }
    result = core.execute(request)
    assert result["collection"] == [{"issue_id": "AGENT-29"}]
    assert calls.index("enter:store") < calls.index("operate:AGENT-30")
    assert calls.index("enter:issue.lock") < calls.index("operate:AGENT-30")
    assert calls.index("operate:AGENT-30") < calls.index("collect")
    assert calls[-1] == "exit:store"
