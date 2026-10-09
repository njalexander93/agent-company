"""Exercise ticket-first startup through the Codex adapter in disposable repositories."""

import json
import subprocess
from pathlib import Path

import pytest

from agent_company.adapters import codex, common, startup
from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.integration


def repository(tmp_path: Path) -> Path:
    """Create an unregistered Git worktree for startup tests."""
    root = tmp_path / "main"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=x@y.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return root


def event(root: Path, name: str, **fields: object) -> dict[str, object]:
    """Build a hook event with its actual test session identity."""
    return {"cwd": str(root), "session_id": "master", "hook_event_name": name, **fields}


def ticket_response() -> dict[str, object]:
    """Model the verified foreground Linear connector result shape."""
    return {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "id": "TEST-1",
                        "uuid": "ticket-uuid",
                        "title": "Test issue",
                        "description": "A ticket description.",
                    }
                ),
            }
        ],
    }


def linear_error(
    message: str = "Could not find referenced Issue.",
    status: int = 400,
    error_code: str = "INVALID_ARGUMENT",
) -> dict[str, object]:
    """Model the observed Linear connector error with narrow variable fields."""
    return {
        "isError": True,
        "structuredContent": {"error_code": error_code},
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "error": "invalid_request",
                        "message": message,
                        "status": status,
                        "requestId": "a4793bad98353233",
                    }
                ),
            }
        ],
    }


def test_ticket_read_precedes_workspace_and_automatically_establishes_readiness(
    tmp_path: Path,
) -> None:
    """A Task line must permit the exact issue read before any local readiness gate."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert not (root / ".task" / "TEST-1").exists()
    call = event(
        root,
        "PreToolUse",
        tool_name="mcp__codex_apps__linear_get_issue",
        tool_input={"id": "TEST-1"},
        tool_use_id="lookup-1",
    )
    assert codex.handle(call) == {}
    assert not (root / ".task" / "TEST-1").exists()
    result = codex.handle(
        event(
            root,
            "PostToolUse",
            tool_name="mcp__codex_apps__linear_get_issue",
            tool_input={"id": "TEST-1"},
            tool_use_id="lookup-1",
            tool_response=ticket_response(),
        )
    )
    assert "TASK_WORKSPACE_NOT_READY" not in str(result)
    assert (root / ".task" / "TEST-1" / "roadmap.md").exists()


def lookup(root: Path, session: str, response: object) -> dict[str, object]:
    """Run one exact direct lookup and matching completion callback."""
    fields = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "lookup-1",
    }
    assert codex.handle(event(root, "PreToolUse", session_id=session, **fields)) == {}
    return codex.handle(
        event(
            root,
            "PostToolUse",
            session_id=session,
            tool_response=response,
            **fields,
        )
    )


@pytest.mark.parametrize(
    ("response", "code"),
    [
        ({"isError": True, "code": "NOT_FOUND"}, "ISSUE_NOT_FOUND for TEST-1"),
        ({"isError": True, "code": "UNAUTHORIZED"}, "PROVIDER_AUTH_REQUIRED"),
        ({"isError": True, "code": "FORBIDDEN"}, "PROVIDER_PERMISSION_DENIED"),
        ({"isError": True, "code": "NETWORK_ERROR"}, "PROVIDER_NETWORK_ERROR"),
        (linear_error(), "Linear could not find requested issue TEST-1."),
        (linear_error(message="Invalid query argument."), "PROVIDER_ERROR"),
        (linear_error(status=401), "PROVIDER_ERROR"),
        (linear_error(error_code="AUTH_REQUIRED"), "PROVIDER_ERROR"),
        ({"isError": True, "content": [{"type": "text", "text": "not found"}]}, "PROVIDER_ERROR"),
        ({"isError": False, "content": []}, "PROVIDER_RESPONSE_INVALID"),
    ],
)
def test_provider_failures_create_no_workspace(
    tmp_path: Path,
    response: object,
    code: str,
) -> None:
    """Recognize only verified missing-reference shapes; preserve other failures."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    result = lookup(root, "master", response)
    assert code in result["systemMessage"]
    assert not (root / ".task" / "TEST-1").exists()


def test_repeat_start_preserves_roadmap_and_coordinator(tmp_path: Path) -> None:
    """The same session can resume without replacing approved task content."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]
    roadmap = (root / ".task" / "TEST-1" / "roadmap.md").read_bytes()
    key = core.participant_key({"host": "codex", "session_id": "master"})
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]
    assert (root / ".task" / "TEST-1" / "roadmap.md").read_bytes() == roadmap
    base = {
        "schema_version": 1,
        "worktree": str(root),
        "host": "codex",
        "session_id": "master",
        "issue_id": "TEST-1",
        "repo_id": json.loads((root / ".task" / ".repository.json").read_text())["repo_id"],
    }
    with core.Store(base) as store, store.issues.child("TEST-1") as control, control.lock():
        issue = core.Issue(store, control, "TEST-1")
        issue.recover()
        assert issue.committed_state()["coordinator"] == key


def test_other_session_joins_as_reader_without_taking_coordinator(tmp_path: Path) -> None:
    """A new session becomes ready with roadmap scope and no write ownership."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    master = common.request_for(event(root, "SessionStart"), "update", "codex")
    roadmap_path = root / ".task" / "TEST-1" / "roadmap.md"
    updated = core.execute(
        {
            **master,
            "operation": "update",
            "expected_revision": core.execute({**master, "operation": "diagnose"})["revision"],
            "path": "roadmap.md",
            "old_digest": core.sha(roadmap_path.read_bytes()),
            "content": "# Approved plan\n",
            "provenance": {
                "sources": [
                    {
                        "id": "approval",
                        "locator": "fixture:approval",
                        "sha256": core.sha(b"approved"),
                    }
                ],
                "applicability": "issue",
                "status": "approved",
            },
        }
    )
    assert updated["ok"], updated
    roadmap = (root / ".task" / "TEST-1" / "roadmap.md").read_bytes()
    codex.handle(event(root, "UserPromptSubmit", session_id="other", prompt="Task: TEST-1"))
    result = lookup(root, "other", ticket_response())
    assert "TASK_WORKSPACE_READY" in result["systemMessage"]
    key = core.participant_key({"host": "codex", "session_id": "master"})
    assert key in result["systemMessage"]
    assert "explicit coordinator transfer" in result["systemMessage"]
    base = {
        "schema_version": 1,
        "worktree": str(root),
        "host": "codex",
        "session_id": "master",
        "issue_id": "TEST-1",
        "repo_id": json.loads((root / ".task" / ".repository.json").read_text())["repo_id"],
    }
    with core.Store(base) as store, store.issues.child("TEST-1") as control, control.lock():
        issue = core.Issue(store, control, "TEST-1")
        issue.recover()
        state = issue.committed_state()
        assert state["coordinator"] == key
        assert state["owners"] == {"roadmap.md": key}
        other_key = core.participant_key({"host": "codex", "session_id": "other"})
        assert [ref["locator"] for ref in state["participants"][other_key]["packet"]] == [
            "roadmap.md"
        ]
    assert (root / ".task" / "TEST-1" / "roadmap.md").read_bytes() == roadmap
    codex.handle(event(root, "UserPromptSubmit", session_id="other", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "other", ticket_response())["systemMessage"]


def test_join_rejects_wrong_uuid_and_caller_scope(tmp_path: Path) -> None:
    """The reader join cannot choose arbitrary notes or bypass issue identity."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    codex.handle(event(root, "UserPromptSubmit", session_id="other", prompt="Task: TEST-1"))
    wrong = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "id": "TEST-1",
                        "uuid": "wrong-uuid",
                    }
                ),
            }
        ],
    }
    assert "ISSUE_MISMATCH" in lookup(root, "other", wrong)["systemMessage"]
    base = common.request_for(event(root, "SessionStart"), "read", "codex")
    rogue = {
        "schema_version": 1,
        "request_id": "rogue-join",
        "operation": "join",
        "worktree": str(root),
        "host": "codex",
        "session_id": "other",
        "repo_id": base["repo_id"],
        "issue_id": "TEST-1",
        "issue_uuid": "ticket-uuid",
        "packet": [{"locator": "context/private.md"}],
    }
    assert core.execute(rogue)["code"] == "INVALID_REQUEST"
    shell_join = {key: value for key, value in rogue.items() if key != "packet"}
    assert not common.canonical_bootstrap(
        event(root, "PreToolUse"),
        common.bootstrap_command(shell_join, "codex"),
        "codex",
        ready=False,
    )


def test_resume_refreshes_only_committed_coordinator_roadmap(tmp_path: Path) -> None:
    """A normal roadmap update can resume; an external edit remains a conflict."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    request = common.request_for(event(root, "SessionStart"), "update", "codex")
    roadmap = root / ".task" / "TEST-1" / "roadmap.md"
    diagnosis = core.execute({**request, "operation": "diagnose"})
    result = core.execute(
        {
            **request,
            "operation": "update",
            "expected_revision": diagnosis["revision"],
            "path": "roadmap.md",
            "old_digest": core.sha(roadmap.read_bytes()),
            "content": "# Approved roadmap\n",
            "provenance": {
                "sources": [
                    {
                        "id": "approval",
                        "locator": "fixture:roadmap-approval",
                        "sha256": core.sha(b"fixture approval"),
                    }
                ],
                "applicability": "issue",
                "status": "approved",
            },
        }
    )
    assert result["ok"], result
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]
    assert roadmap.read_text() == "# Approved roadmap\n"
    roadmap.write_text("external mutation")
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "UNTRACKED_CHANGE" in lookup(root, "master", ticket_response())["systemMessage"]


def test_ordinary_issue_read_after_start_is_not_a_new_bootstrap(tmp_path: Path) -> None:
    """A later issue read follows ordinary tracked-tool admission and completion."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    fields = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "OTHER-2"},
        "tool_use_id": "ordinary-read",
    }
    assert codex.handle(event(root, "PreToolUse", **fields)) == {}
    assert (
        codex.handle(event(root, "PostToolUse", tool_response={"isError": False}, **fields)) == {}
    )


def test_completion_retry_after_partial_setup_preserves_correlation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed setup completion can retry with the same observed tool ID."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    original = startup._call
    calls = 0

    def fail_once(request: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        if request["operation"] == "scope" and calls == 0:
            calls += 1
            raise core.WorkspaceError("BUSY")
        return original(request)

    monkeypatch.setattr(startup, "_call", fail_once)
    fields = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "lookup-1",
    }
    assert codex.handle(event(root, "PreToolUse", **fields)) == {}
    completed = event(root, "PostToolUse", tool_response=ticket_response(), **fields)
    assert "BUSY" in codex.handle(completed)["systemMessage"]
    assert (root / ".task" / "TEST-1" / "roadmap.md").exists()
    assert "TASK_WORKSPACE_READY" in codex.handle(completed)["systemMessage"]


def test_linked_checkout_supplies_governing_sources(tmp_path: Path) -> None:
    """Task bytes come from main; governing files come from the selected checkout."""
    main = repository(tmp_path)
    other = tmp_path / "other"
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "-q", "-b", "other", str(other)], check=True
    )
    (other / "AGENTS.md").write_text("# Selected checkout rules\n")
    codex.handle(event(other, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(other, "master", ticket_response())["systemMessage"]
    request = common.request_for(event(other, "SessionStart"), "read", "codex")
    refs = core.execute(request)["references"]
    assert any(ref["locator"] == str(other / "AGENTS.md") for ref in refs)
    assert (main / ".task" / "TEST-1" / "roadmap.md").exists()


def test_optional_missing_packet_source_does_not_block_resume(tmp_path: Path) -> None:
    """Read the available references and leave an optional absent source absent."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    base = common.request_for(event(root, "SessionStart"), "read", "codex")
    read = core.execute(base)
    packet = [
        {key: value for key, value in ref.items() if key != "available"}
        for ref in read["references"]
    ]
    packet.append(
        {
            "id": "optional",
            "locator": "context/missing.md",
            "sha256": "0" * 64,
            "required": False,
            "authority": "task-workspace",
            "reason": "optional-context",
            "stage": "planning",
            "reader": core.participant_key(base),
        }
    )
    scoped = core.execute(
        {
            **base,
            "operation": "scope",
            "expected_revision": read["revision"],
            "target_participant": core.participant_key(base),
            "packet": packet,
        }
    )
    assert scoped["ok"], scoped
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]


def test_lookup_requires_exact_ticket_and_tool_correlation(tmp_path: Path) -> None:
    """A different identifier or completion tool ID cannot establish readiness."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    wrong = codex.handle(
        event(
            root,
            "PreToolUse",
            tool_name="mcp__codex_apps__linear_get_issue",
            tool_input={"id": "OTHER-2"},
            tool_use_id="lookup-1",
        )
    )
    assert "BINDING_CONFLICT" in str(wrong)
    fields = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "lookup-1",
    }
    assert codex.handle(event(root, "PreToolUse", **fields)) == {}
    mismatched = codex.handle(
        event(
            root,
            "PostToolUse",
            tool_response=ticket_response(),
            **{**fields, "tool_use_id": "lookup-2"},
        )
    )
    assert "BINDING_CONFLICT" in mismatched["systemMessage"]
    assert not (root / ".task" / "TEST-1").exists()


def test_reader_resume_refreshes_only_core_roadmap_packet(tmp_path: Path) -> None:
    """A joined reader stays ready after the coordinator's committed update."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    codex.handle(event(root, "UserPromptSubmit", session_id="other", prompt="Task: TEST-1"))
    lookup(root, "other", ticket_response())
    request = common.request_for(event(root, "SessionStart"), "update", "codex")
    roadmap = root / ".task" / "TEST-1" / "roadmap.md"
    updated = core.execute(
        {
            **request,
            "operation": "update",
            "expected_revision": core.execute({**request, "operation": "diagnose"})["revision"],
            "path": "roadmap.md",
            "old_digest": core.sha(roadmap.read_bytes()),
            "content": "# New approved revision\n",
            "provenance": {
                "sources": [
                    {
                        "id": "approval",
                        "locator": "fixture:approval",
                        "sha256": core.sha(b"approved"),
                    }
                ],
                "applicability": "issue",
                "status": "approved",
            },
        }
    )
    assert updated["ok"], updated
    codex.handle(event(root, "UserPromptSubmit", session_id="other", prompt="Task: TEST-1"))
    assert "TASK_WORKSPACE_READY" in lookup(root, "other", ticket_response())["systemMessage"]


def test_malformed_completed_result_allows_new_lookup_id(tmp_path: Path) -> None:
    """A completed malformed provider result does not strand lookup correlation."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    first = {
        "tool_name": "mcp__codex_apps__linear_get_issue",
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "first",
    }
    assert codex.handle(event(root, "PreToolUse", **first)) == {}
    failed = codex.handle(event(root, "PostToolUse", tool_response="not JSON", **first))
    assert "PROVIDER_RESPONSE_INVALID" in failed["systemMessage"]
    second = {**first, "tool_use_id": "second"}
    assert codex.handle(event(root, "PreToolUse", **second)) == {}
    completed = codex.handle(event(root, "PostToolUse", tool_response=ticket_response(), **second))
    assert "TASK_WORKSPACE_READY" in completed["systemMessage"]


def test_new_task_line_fences_previous_ready_session_until_ticket_read(tmp_path: Path) -> None:
    """A ready binding cannot admit ordinary tools for a newly requested Task."""
    root = repository(tmp_path)
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    lookup(root, "master", ticket_response())
    codex.handle(event(root, "UserPromptSubmit", prompt="Task: TEST-1"))
    for tool_name, tool_input in (
        ("Read", {"file_path": str(root / "AGENTS.md")}),
        ("exec_command", {"cmd": "pwd", "login": False, "shell": "/bin/sh"}),
    ):
        denied = codex.handle(
            event(
                root,
                "PreToolUse",
                tool_name=tool_name,
                tool_input=tool_input,
                tool_use_id="blocked-" + tool_name,
            )
        )
        assert "TICKET_READ_REQUIRED" in str(denied)
    assert "TASK_WORKSPACE_READY" in lookup(root, "master", ticket_response())["systemMessage"]
