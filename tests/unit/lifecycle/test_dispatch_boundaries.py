"""Check cleanup and permission dispatch without real repository or filesystem access."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def test_collect_candidates_uses_separate_sessions_and_bounded_requests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Send only explicit candidate requests with a per-issue cleanup request ID."""
    requests: list[dict[str, Any]] = []

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        """Record one delegated cleanup request and return its published result."""
        requests.append(request)
        return {"ok": True, "code": "CLEANED"}

    monkeypatch.setattr(core, "execute", execute)
    request: dict[str, Any] = {
        "cleanup_candidates": [
            {"issue_id": "AGENT-29", "session_id": "maintenance", "host": "codex"}
        ],
        "issue_id": "AGENT-30",
        "request_id": "create-1",
        "session_id": "owner",
        "worktree": str(tmp_path / "checkout"),
        "repo_id": "repo",
    }
    assert core.collect_candidates(object(), request) == [
        {"issue_id": "AGENT-29", "ok": True, "code": "CLEANED"}
    ]
    assert requests == [
        {
            "issue_id": "AGENT-29",
            "session_id": "maintenance",
            "host": "codex",
            "schema_version": 1,
            "operation": "cleanup-commit",
            "request_id": "create-1:collect:AGENT-29",
            "worktree": str(tmp_path / "checkout"),
            "repo_id": "repo",
        }
    ]


@pytest.mark.parametrize(
    ("candidates", "code"),
    [
        ([{"issue_id": "AGENT-30", "session_id": "maintenance"}], "INVALID_REQUEST"),
        ([{"issue_id": "AGENT-29", "session_id": "owner"}], "BINDING_CONFLICT"),
        ([{"issue_id": "bad", "session_id": "maintenance"}], "INVALID_ISSUE"),
    ],
)
def test_collect_candidates_rejects_wrong_scope_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    candidates: list[dict[str, str]],
    code: str,
) -> None:
    """Do not touch another issue for self-cleanup, same session, or invalid ID."""
    monkeypatch.setattr(core, "execute", lambda _request: pytest.fail("dispatched cleanup"))
    with pytest.raises(core.WorkspaceError) as captured:
        core.collect_candidates(
            object(),
            {
                "cleanup_candidates": candidates,
                "issue_id": "AGENT-30",
                "request_id": "create-1",
                "session_id": "owner",
                "worktree": str(tmp_path / "checkout"),
                "repo_id": "repo",
            },
        )
    assert captured.value.code == code


def test_permission_paths_selects_only_issue_control_and_binding_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Derive the narrow retry set from a checked root and explicit issue ID."""
    root, main = tmp_path / "canonical", tmp_path / "main"
    monkeypatch.setattr(
        core,
        "repository",
        lambda _root: (root, tmp_path / "common", [root]),
    )
    assert core.permission_paths(
        {"worktree": str(tmp_path / "alias"), "main_worktree": str(main), "issue_id": "AGENT-30"}
    ) == [
        str(root / ".task/.repository.json"),
        str(root / ".task/.bindings"),
        str(main / ".task/.control/repository.json"),
        str(main / ".task/AGENT-30"),
        str(main / ".task/.control/issues/AGENT-30"),
    ]


def test_permission_paths_omits_unvalidated_issue_for_nonregister_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An invalid issue ID cannot widen permission requests to issue directories."""
    root, main = tmp_path / "canonical", tmp_path / "main"
    monkeypatch.setattr(
        core,
        "repository",
        lambda _root: (root, tmp_path / "common", [root]),
    )
    assert core.permission_paths(
        {
            "worktree": str(tmp_path / "alias"),
            "main_worktree": str(main),
            "issue_id": "bad",
            "operation": "ready",
        }
    ) == [
        str(root / ".task/.repository.json"),
        str(root / ".task/.bindings"),
        str(main / ".task/.control/repository.json"),
    ]


def test_permission_paths_retains_requested_root_when_repository_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep a bounded registration retry path when Git identity is unavailable."""
    root, main = tmp_path / "checkout", tmp_path / "main"
    monkeypatch.setattr(core, "repository", lambda _root: (_ for _ in ()).throw(OSError("git")))
    assert core.permission_paths(
        {"worktree": str(root), "main_worktree": str(main), "operation": "register"}
    ) == [
        str(root / ".task/.repository.json"),
        str(root / ".task/.bindings"),
        str(main / ".task/.control/repository.json"),
        str(main / ".task"),
        str(main / ".task/.control"),
    ]


def test_permission_paths_discovers_main_only_from_validated_local_registration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Read the canonical store path through direct handles when request omits it."""
    root, main = tmp_path / "checkout", tmp_path / "main"
    monkeypatch.setattr(core, "repository", lambda _root: (root, None, []))

    class Node:
        """Expose one validated local registration document."""

        def __enter__(self) -> Node:
            """Hold the modeled owned directory."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled owned directory."""

        def child(self, name: str) -> Node:
            """Open only the direct task directory."""
            assert name == ".task"
            return self

        def json(self, name: str) -> dict[str, str]:
            """Read only the local repository registration."""
            assert name == ".repository.json"
            return {"main": str(main)}

    monkeypatch.setattr(core.Directory, "absolute", lambda _root: Node())
    assert core.permission_paths({"worktree": str(root), "issue_id": "AGENT-30"}) == [
        str(root / ".task/.repository.json"),
        str(root / ".task/.bindings"),
        str(main / ".task/.control/repository.json"),
        str(main / ".task/AGENT-30"),
        str(main / ".task/.control/issues/AGENT-30"),
    ]


def test_permission_paths_does_not_invent_main_when_registration_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep retry paths at the selected checkout if local registration cannot be read."""
    root = tmp_path / "checkout"
    monkeypatch.setattr(core, "repository", lambda _root: (root, None, []))
    monkeypatch.setattr(
        core.Directory,
        "absolute",
        lambda _root: (_ for _ in ()).throw(PermissionError("denied")),
    )
    assert core.permission_paths({"worktree": str(root), "issue_id": "AGENT-30"}) == [
        str(root / ".task/.repository.json"),
        str(root / ".task/.bindings"),
    ]


def test_execute_dispatches_rebind_under_session_lock_without_opening_single_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public executor routes rebind to the two-issue transaction under binding lock."""
    locks: list[str] = []

    class BindingLock:
        """Record the session lock acquired before rebind."""

        def __enter__(self) -> BindingLock:
            """Hold the modeled lock."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the modeled lock."""

    class Bindings:
        """Expose only the selected session lock."""

        def lock(self, name: str) -> BindingLock:
            """Record the exact binding lock name."""
            locks.append(name)
            return BindingLock()

    class Store:
        """Expose only the rebind's session serialization handle."""

        def __init__(self, _request: dict[str, Any]) -> None:
            """Select the modeled registered store."""
            self.bindings = Bindings()

        def __enter__(self) -> Store:
            """Hold the store."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the store."""

    expected = {"ok": True, "code": "BOUND", "binding_generation": 3}
    monkeypatch.setattr(core, "Store", Store)
    monkeypatch.setattr(core, "rebind", lambda _store, _request: expected)
    request = {
        "schema_version": 1,
        "operation": "rebind",
        "request_id": "rebind-1",
        "issue_id": "AGENT-30",
        "repo_id": "repo",
        "host": "codex",
        "session_id": "session",
    }
    assert core.execute(request) is expected
    assert locks == [core.participant_key(request) + ".lock"]


def test_execute_collects_only_after_successful_create_with_explicit_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public create dispatch appends candidate cleanup only when caller supplies it."""
    calls: list[str] = []

    class Node:
        """Provide binding and issue locks without native filesystem access."""

        def __enter__(self) -> Node:
            """Hold one modeled lock or issue handle."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release one modeled handle."""

        def lock(self, _name: str | None = None) -> Node:
            """Acquire a modeled lock."""
            return self

        def child(self, _identifier: str, _create: bool = False) -> Node:
            """Select the requested issue."""
            return self

    class Store:
        """Expose only handles needed for public create dispatch."""

        def __init__(self, _request: dict[str, Any]) -> None:
            """Select the registered store."""
            self.bindings = Node()
            self.issues = Node()

        def __enter__(self) -> Store:
            """Hold the store."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Release the store."""

    def operate(_store: Store, _issue: object, _request: dict[str, Any]) -> dict[str, Any]:
        """Model one successful committed create."""
        calls.append("create")
        return {"ok": True, "code": "CREATED"}

    def collect(_store: Store, _request: dict[str, Any]) -> list[dict[str, Any]]:
        """Record only the caller-requested follow-on maintenance."""
        calls.append("collect")
        return [{"issue_id": "AGENT-29", "ok": True}]

    monkeypatch.setattr(core, "Store", Store)
    monkeypatch.setattr(core, "Issue", lambda *_args: object())
    monkeypatch.setattr(core, "operate", operate)
    monkeypatch.setattr(core, "collect_candidates", collect)
    request = {
        "schema_version": 1,
        "operation": "create",
        "request_id": "create-1",
        "issue_id": "AGENT-30",
        "repo_id": "repo",
        "host": "codex",
        "session_id": "session",
    }
    assert core.execute(request) == {"ok": True, "code": "CREATED"}
    assert calls == ["create"]
    result = core.execute({**request, "cleanup_candidates": [{"issue_id": "AGENT-29"}]})
    assert result["collection"] == [{"issue_id": "AGENT-29", "ok": True}]
    assert calls == ["create", "create", "collect"]
