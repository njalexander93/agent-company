"""Complete a Codex issue start after a verified foreground Linear issue read."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from agent_company.lifecycle import task_workspace as core


def _linear_missing_issue(response: dict[str, object]) -> bool:
    """Recognize the observed Linear missing-reference envelope, not generic 400s."""
    if response.get("structuredContent") != {"error_code": "INVALID_ARGUMENT"}:
        return False
    content = response.get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        return False
    if content[0].get("type") != "text" or not isinstance(content[0].get("text"), str):
        return False
    try:
        detail = json.loads(content[0]["text"])
    except ValueError:
        return False
    return (
        isinstance(detail, dict)
        and detail.get("error") == "invalid_request"
        and type(detail.get("status")) is int
        and detail["status"] == 400
        and detail.get("message") == "Could not find referenced Issue."
        and isinstance(detail.get("requestId"), str)
        and bool(detail["requestId"])
    )


def ticket(response: object, identifier: str) -> core.JSONObject:
    """Extract the exact requested issue from a successful connector result.

    Raises:
        core.WorkspaceError: The provider failed, confirmed absence, or returned unusable data.
    """
    if not isinstance(response, dict):
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    if response.get("isError") is True:
        # Exact provider shapes are needed; generic invalid requests are not absence.
        code = response.get("code")
        if isinstance(code, str) and code in {"NOT_FOUND", "ISSUE_NOT_FOUND"}:
            raise core.WorkspaceError("ISSUE_NOT_FOUND")
        if _linear_missing_issue(response):
            raise core.WorkspaceError("LINEAR_ISSUE_UNRESOLVED")
        failures = {
            "UNAUTHORIZED": "PROVIDER_AUTH_REQUIRED",
            "FORBIDDEN": "PROVIDER_PERMISSION_DENIED",
            "NETWORK_ERROR": "PROVIDER_NETWORK_ERROR",
            "TIMEOUT": "PROVIDER_NETWORK_ERROR",
        }
        raise core.WorkspaceError(
            failures.get(code, "PROVIDER_ERROR") if isinstance(code, str) else "PROVIDER_ERROR"
        )
    if response.get("isError") is not False:
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    content = response.get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    if content[0].get("type") != "text" or not isinstance(content[0].get("text"), str):
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    try:
        issue = json.loads(content[0]["text"])
    except ValueError as error:
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID") from error
    if not isinstance(issue, dict) or issue.get("id") != identifier:
        raise core.WorkspaceError("ISSUE_MISMATCH")
    if not isinstance(issue.get("uuid"), str):
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    core.token(issue["uuid"])
    return issue


def _call(request: core.JSONObject) -> core.JSONObject:
    """Require a successful lifecycle result while retaining its exact failure code."""
    result = core.execute({"schema_version": 1, "request_id": str(uuid.uuid4()), **request})
    if not result["ok"]:
        raise core.WorkspaceError(result["code"])
    return result


def _read_file(path: Path) -> bytes:
    """Read an assigned source through the lifecycle's no-follow filesystem API."""
    with core.Directory.absolute(path.parent) as parent:
        return parent.read(path.name)


def _initial_packet(checkout: Path, main: Path, issue_id: str, reader: str) -> list[core.SourceRef]:
    """Build the packet from canonical task bytes and the selected checkout's rules."""
    sources = [
        ("roadmap", "roadmap.md", main / ".task" / issue_id / "roadmap.md"),
        ("contributors", str(checkout / "AGENTS.md"), checkout / "AGENTS.md"),
        (
            "procedure",
            str(checkout / "docs/runtime/contributor-workflow.md"),
            checkout / "docs/runtime/contributor-workflow.md",
        ),
    ]
    packet: list[core.SourceRef] = []
    for source_id, locator, path in sources:
        if source_id != "roadmap" and not path.is_file():
            continue
        data = _read_file(path)
        packet.append(
            {
                "id": source_id,
                "locator": locator,
                "sha256": core.sha(data),
                "required": True,
                "authority": "repository-governing" if source_id != "roadmap" else "task-workspace",
                "reason": "issue-startup",
                "stage": "planning",
                "reader": reader,
            }
        )
    return packet


def start(event: core.JSONObject, issue: core.JSONObject, host: str = "codex") -> core.JSONObject:
    """Register or resume the actual session, then read and acknowledge its packet.

    The caller must have verified the direct foreground provider result with ``ticket``.
    Existing coordinator, packet, roadmap, and approval remain unchanged.
    """
    identifier = issue["id"]
    core.issue_id(identifier)
    root, _, trees = core.repository(event["cwd"])
    base: core.JSONObject = {
        "worktree": str(root),
        "host": host,
        "session_id": core.token(event["session_id"]),
        "issue_id": identifier,
        "issue_uuid": issue["uuid"],
    }
    registered = _call(
        {
            "operation": "register",
            "worktree": str(root),
            "main_worktree": str(trees[0]),
            "host": host,
            "session_id": base["session_id"],
        }
    )
    base["repo_id"] = registered["repo_id"]
    diagnosis = _call({**base, "operation": "diagnose"})
    key = core.participant_key(base)
    if diagnosis["code"] == "ABSENT":
        attached = _call({**base, "operation": "create", "coordinator": key})
        base["binding_generation"] = attached["binding_generation"]
        revision = _call({**base, "operation": "diagnose"})["revision"]
        _call(
            {
                **base,
                "operation": "scope",
                "expected_revision": revision,
                "target_participant": key,
                "packet": _initial_packet(root, trees[0], identifier, key),
            }
        )
    else:
        with core.Store(base) as store, store.issues.child(identifier) as control, control.lock():
            current = core.Issue(store, control, identifier)
            current.recover()
            state = current.committed_state()
            if state["issue_uuid"] != issue["uuid"]:
                raise core.WorkspaceError("ISSUE_MISMATCH")
            participant = state["participants"].get(key)
            needs_join = (
                key != state["coordinator"]
                and not participant
                and key not in state.get("assignments", {})
            )
            if participant:
                base["binding_generation"] = participant["generation"]
        attached = _call(
            {
                **base,
                "operation": "join" if needs_join else "resume",
                **({"expected_revision": state["revision"]} if needs_join else {}),
            }
        )
        base["binding_generation"] = attached["binding_generation"]
        with core.Store(base) as store, store.issues.child(identifier) as control, control.lock():
            current = core.Issue(store, control, identifier)
            current.recover()
            state = current.committed_state()
            current.files()  # Verify the committed manifest before refreshing any packet digest.
            participant = state["participants"][key]
            missing_packet = participant["packet"] is None
        if missing_packet:
            if key != state["coordinator"]:
                raise core.WorkspaceError("SCOPE_MISSING")
            revision = _call({**base, "operation": "diagnose"})["revision"]
            _call(
                {
                    **base,
                    "operation": "scope",
                    "expected_revision": revision,
                    "target_participant": key,
                    "packet": _initial_packet(root, trees[0], identifier, key),
                }
            )
        elif key != state["coordinator"] and participant["packet"] is not None:
            packet = participant["packet"]
            if (
                len(packet) == 1
                and packet[0]["locator"] == "roadmap.md"
                and packet[0]["reason"] == "issue-resume"
                and packet[0]["sha256"] != state["files"]["roadmap.md"]
            ):
                _call({**base, "operation": "join", "expected_revision": state["revision"]})
        elif key == state["coordinator"] and state["owners"]["roadmap.md"] == key:
            # A coordinator's normal lifecycle update changes the canonical roadmap
            # digest. Refresh only that owned reference; external edits fail files().
            assert participant["packet"] is not None
            packet = [ref.copy() for ref in participant["packet"]]
            changed = False
            for ref in packet:
                if ref["locator"] == "roadmap.md" and ref["sha256"] != state["files"]["roadmap.md"]:
                    ref["sha256"] = state["files"]["roadmap.md"]
                    changed = True
            if changed:
                _call(
                    {
                        **base,
                        "operation": "scope",
                        "expected_revision": state["revision"],
                        "target_participant": key,
                        "packet": packet,
                    }
                )
    read = _call({**base, "operation": "read"})
    # A descriptor alone is not a delivered source. Read and hash each assigned file.
    for ref in read["references"]:
        if not ref["available"]:
            continue
        locator = Path(ref["locator"])
        path = locator if locator.is_absolute() else trees[0] / ".task" / identifier / locator
        if core.sha(_read_file(path)) != ref["sha256"]:
            raise core.WorkspaceError("SOURCE_STALE")
    _call(
        {
            **base,
            "operation": "acknowledge",
            "expected_revision": read["revision"],
            "packet_digest": read["packet_digest"],
        }
    )
    ready = _call({**base, "operation": "ready"})
    with core.Store(base) as store, store.issues.child(identifier) as control, control.lock():
        current = core.Issue(store, control, identifier)
        current.recover()
        ready["coordinator"] = current.committed_state()["coordinator"]
    ready["participant_id"] = key
    return ready
