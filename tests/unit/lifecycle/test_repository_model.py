"""Check Git identity and store binding decisions with bounded doubles."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


def test_git_uses_argument_vector_and_bounded_noninteractive_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the repository query out of shell interpretation."""
    observed: list[tuple[list[str], dict[str, object]]] = []

    def run(argv: list[str], **options: object) -> SimpleNamespace:
        """Record the exact Git invocation and return a clean response."""
        observed.append((argv, options))
        return SimpleNamespace(returncode=0, stdout="  root\n")

    monkeypatch.setattr(core.subprocess, "run", run)
    assert core.git("/checkout", "rev-parse", "--show-toplevel") == "root"
    argv, options = observed[0]
    assert argv == ["git", "-C", "/checkout", "rev-parse", "--show-toplevel"]
    assert options["timeout"] == 2
    assert options["check"] is False
    assert options["capture_output"] is True


def test_git_rejects_failed_repository_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return a bounded repository diagnostic for Git failure."""
    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=128, stdout="secret detail"),
    )
    with pytest.raises(core.WorkspaceError) as captured:
        core.git("/checkout", "rev-parse")
    assert captured.value.code == "REPOSITORY_MISMATCH"
    assert "secret detail" not in str(captured.value)


def test_repository_requires_nonbare_member_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retain only Git-reported worktree members under one common directory."""
    responses = {
        ("rev-parse", "--show-toplevel"): "/checkout",
        ("rev-parse", "--is-bare-repository"): "false",
        ("rev-parse", "--path-format=absolute", "--git-common-dir"): "/git/common",
        (
            "worktree",
            "list",
            "--porcelain",
            "-z",
        ): "worktree /checkout\0HEAD abc\0worktree /other\0",
    }
    monkeypatch.setattr(core, "git", lambda _path, *args: responses[args])
    assert core.repository("/checkout") == (
        Path("/checkout"),
        Path("/git/common"),
        [Path("/checkout"), Path("/other")],
    )
    responses[("worktree", "list", "--porcelain", "-z")] = "worktree /other\0"
    with pytest.raises(core.WorkspaceError) as captured:
        core.repository("/checkout")
    assert captured.value.code == "REPOSITORY_MISMATCH"


class BindingDirectory:
    """Capture binding writes and issue view requests without filesystem access."""

    def __init__(self) -> None:
        """Start with no stored binding."""
        self.data: dict[str, Any] = {}
        self.views: list[tuple[str, str]] = []
        self.children: list[str] = []

    def exists(self, name: str) -> bool:
        """Report only keys actually stored by this bounded double."""
        return name in self.data

    def json(self, name: str) -> Any:
        """Read a previously stored binding."""
        return self.data[name]

    def put(self, name: str, value: object) -> None:
        """Capture the committed binding value."""
        self.data[name] = value

    def issue_view(self, issue: str, target: str) -> None:
        """Capture the exact issue-only view target."""
        self.views.append((issue, target))

    def child(self, issue: str) -> BindingDirectory:
        """Expose a modeled canonical issue directory."""
        self.children.append(issue)
        return self

    def __enter__(self) -> BindingDirectory:
        """Open the modeled issue handle."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the modeled issue handle."""


def bare_store() -> core.Store:
    """Supply the methods under test with a selected registered identity."""
    store = core.Store.__new__(core.Store)
    store.request = {"host": "codex", "session_id": "session"}
    store.root = Path("/linked")
    store.registration = {"repo_id": "repo", "main": "/main"}
    store.bindings = BindingDirectory()  # type: ignore[assignment]
    store.local = BindingDirectory()  # type: ignore[assignment]
    store.task = BindingDirectory()  # type: ignore[assignment]
    return store


def test_store_binding_does_not_invent_absent_assignment() -> None:
    """Read only the explicit session key and persist its generation."""
    store = bare_store()
    assert store.binding() is None
    key = core.participant_key(store.request)
    state = {"issue_id": "AGENT-30", "participants": {key: {"generation": 3}}}
    store.save_binding(state, key)  # type: ignore[arg-type]
    assert store.binding() == {
        "repo_id": "repo",
        "issue_id": "AGENT-30",
        "participant_id": key,
        "binding_generation": 3,
    }


def test_store_view_exposes_only_selected_issue_link() -> None:
    """Open the canonical issue after publishing its exact linked view."""
    store = bare_store()
    store.view("AGENT-30")
    assert store.local.views == [("AGENT-30", "/main/.task/AGENT-30")]
    assert store.task.children == ["AGENT-30"]
    store.root = Path("/main")
    store.view("AGENT-31")
    assert store.local.views == [("AGENT-30", "/main/.task/AGENT-30")]


def test_store_closes_tracked_handles_in_reverse_open_order() -> None:
    """Release child handles before their parent after success or failure."""
    order: list[str] = []

    class Handle:
        """Record one descriptor release."""

        def __init__(self, name: str) -> None:
            """Name the modeled descriptor."""
            self.name = name

        def close(self) -> None:
            """Record release of this descriptor."""
            order.append(self.name)

    store = core.Store.__new__(core.Store)
    store.handles = []
    parent, child = Handle("parent"), Handle("child")
    assert store.keep(parent) is parent  # type: ignore[arg-type]
    assert store.keep(child) is child  # type: ignore[arg-type]
    store.close()
    assert order == ["child", "parent"]
    assert store.handles == []


class Node:
    """Expose direct named control entries to registration without native I/O."""

    def __init__(
        self,
        path: str,
        documents: dict[str, dict[str, object]],
        closed: list[str] | None = None,
    ) -> None:
        """Share one persistent document map across opened handles."""
        self.path = path
        self.documents = documents
        self.closed = closed
        self.identity = [3, 4]

    def __enter__(self) -> Node:
        """Open this modeled direct directory."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Leave this modeled direct directory."""

    def child(self, name: str, _create: bool = False) -> Node:
        """Open a direct child using the same document map."""
        return Node(self.path + "/" + name, self.documents, self.closed)

    def close(self) -> None:
        """Record native-handle release after use or partial initialization."""
        if self.closed is not None:
            self.closed.append(self.path)

    def lock(self, _name: str) -> Node:
        """Hold a modeled registration lock for this synchronous test."""
        return self

    def exists(self, name: str) -> bool:
        """Check only a direct document key."""
        return name in self.documents.get(self.path, {})

    def json(self, name: str) -> object:
        """Read an already published direct document."""
        return self.documents[self.path][name]

    def put(self, name: str, value: object) -> None:
        """Publish a direct document for later registration read-back."""
        self.documents.setdefault(self.path, {})[name] = value


def test_register_persists_and_reuses_exact_repository_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the same repository ID on a matching registration retry."""
    documents: dict[str, dict[str, object]] = {}
    root = Path("/main")
    common = Path("/git/common")
    monkeypatch.setattr(core, "repository", lambda _path: (root, common, [root]))
    monkeypatch.setattr(
        core, "fs_identity", lambda path: [1, 2] if Path(path) == common else [3, 4]
    )
    monkeypatch.setattr(core.Directory, "absolute", lambda path: Node(str(path), documents))
    request = {
        "worktree": str(root),
        "main_worktree": str(root),
        "host": "codex",
        "session_id": "s",
    }
    first = core.register(request)
    second = core.register(request)
    assert first == second
    assert first["code"] == "REGISTERED"
    assert (
        documents["/main/.task/.control"]["repository.json"]
        == documents["/main/.task"][".repository.json"]
    )


def test_register_refuses_changed_local_identity_without_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve a conflicting local registration for explicit recovery."""
    documents: dict[str, dict[str, object]] = {}
    root = Path("/main")
    common = Path("/git/common")
    monkeypatch.setattr(core, "repository", lambda _path: (root, common, [root]))
    monkeypatch.setattr(
        core, "fs_identity", lambda path: [1, 2] if Path(path) == common else [3, 4]
    )
    monkeypatch.setattr(core.Directory, "absolute", lambda path: Node(str(path), documents))
    request = {
        "worktree": str(root),
        "main_worktree": str(root),
        "host": "codex",
        "session_id": "s",
    }
    core.register(request)
    documents["/main/.task"][".repository.json"] = {"repo_id": "foreign"}
    with pytest.raises(core.WorkspaceError) as captured:
        core.register(request)
    assert captured.value.code == "REPOSITORY_MISMATCH"
    assert documents["/main/.task"][".repository.json"] == {"repo_id": "foreign"}


def test_store_initialization_verifies_registration_and_releases_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate local and canonical registrations before exposing store handles."""
    documents: dict[str, dict[str, object]] = {}
    closed: list[str] = []
    root = Path("/main")
    common = Path("/git/common")
    monkeypatch.setattr(core, "repository", lambda _path: (root, common, [root]))
    monkeypatch.setattr(
        core, "fs_identity", lambda path: [1, 2] if Path(path) == common else [3, 4]
    )
    monkeypatch.setattr(core.Directory, "absolute", lambda path: Node(str(path), documents, closed))
    registration = core.register(
        {"worktree": str(root), "main_worktree": str(root), "host": "codex", "session_id": "s"}
    )
    with core.Store({"worktree": str(root), "repo_id": registration["repo_id"]}) as store:
        assert store.registration["repo_id"] == registration["repo_id"]
        assert len(store.handles) == 7
    assert store.handles == []
    assert closed[-2:] == ["/main/.task", "/main"]


def test_store_initialization_closes_partial_handles_on_repo_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a foreign request ID and close already opened local descriptors."""
    documents: dict[str, dict[str, object]] = {}
    closed: list[str] = []
    root = Path("/main")
    common = Path("/git/common")
    monkeypatch.setattr(core, "repository", lambda _path: (root, common, [root]))
    monkeypatch.setattr(
        core, "fs_identity", lambda path: [1, 2] if Path(path) == common else [3, 4]
    )
    monkeypatch.setattr(core.Directory, "absolute", lambda path: Node(str(path), documents, closed))
    core.register(
        {"worktree": str(root), "main_worktree": str(root), "host": "codex", "session_id": "s"}
    )
    with pytest.raises(core.WorkspaceError) as captured:
        core.Store({"worktree": str(root), "repo_id": "foreign"})
    assert captured.value.code == "REPOSITORY_MISMATCH"
    assert closed[-2:] == ["/main/.task", "/main"]


def test_register_persists_only_explicit_coordinator_startup_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind startup to the observed session and preserve an identical retry."""
    documents: dict[str, dict[str, object]] = {}
    root, common = Path("/main"), Path("/git/common")
    monkeypatch.setattr(core, "repository", lambda _path: (root, common, [root]))
    monkeypatch.setattr(
        core, "fs_identity", lambda path: [1, 2] if Path(path) == common else [3, 4]
    )
    monkeypatch.setattr(core.Directory, "absolute", lambda path: Node(str(path), documents))
    request = {
        "worktree": str(root),
        "main_worktree": str(root),
        "host": "codex",
        "session_id": "session",
    }
    key = core.participant_key(request)
    packet = [
        {
            "id": "roadmap",
            "locator": "roadmap.md",
            "sha256": core.sha(b"roadmap"),
            "required": True,
            "authority": "task-workspace",
            "reason": "issue-startup",
            "stage": "planning",
            "reader": key,
        }
    ]
    setup = {"issue_id": "AGENT-30", "issue_uuid": "uuid", "packet": packet, "coordinator": key}
    first = core.register({**request, "startup": setup})
    assert first["code"] == "REGISTERED"
    binding_path = "/main/.task/.bindings"
    assert documents[binding_path][key + ".startup.json"] == setup
    assert core.register({**request, "startup": setup})["repo_id"] == first["repo_id"]
    altered = {**setup, "issue_id": "AGENT-31"}
    with pytest.raises(core.WorkspaceError) as captured:
        core.register({**request, "startup": altered})
    assert captured.value.code == "BINDING_CONFLICT"
    assert documents[binding_path][key + ".startup.json"] == setup
