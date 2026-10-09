"""Register any supported host session after its verified foreground Linear read.

This module validates ticket identity and drives the shared local lifecycle. The
native adapters own callback admission and provider provenance boundaries.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from agent_company.lifecycle import task_workspace as core


def _linear_missing_issue(response: dict[str, object]) -> bool:
    """Recognize the observed Linear missing-reference envelope, not generic 400s.

    Args:
        response: Failed Codex connector result to classify narrowly.

    Returns:
        Whether the observed connector error matches this exact unresolved issue shape.
    """
    # Require the observed structured error marker before inspecting its text.
    if response.get("structuredContent") != {"error_code": "INVALID_ARGUMENT"}:
        return False
    content = response.get("content")
    # The observed error has exactly one text content block.
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        return False
    # Reject content types that cannot carry the structured error detail.
    if content[0].get("type") != "text" or not isinstance(content[0].get("text"), str):
        return False
    # Parse the single text detail and require every distinguishing field.
    try:
        detail = json.loads(content[0]["text"])
    # Malformed error detail cannot prove that the issue is missing.
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


def _normalize_issue(issue: object, identifier: str) -> core.JSONObject:
    """Validate one issue object under either supported identifier convention.

    Args:
        issue: Parsed provider issue object.
        identifier: Exact shorthand identifier requested by the session.

    Returns:
        An issue with shorthand ``id`` and canonical immutable ``uuid``.

    Raises:
        core.WorkspaceError: If the issue identity or UUID is invalid.
    """
    # Separate the existing Codex connector shape from Linear's GraphQL fields.
    if not isinstance(issue, dict):
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    # Translate GraphQL identity fields into the shared ticket contract.
    if "identifier" in issue:
        normalized = {**issue, "uuid": issue.get("id"), "id": issue["identifier"]}
    else:
        # The existing Codex connector already uses shorthand id and UUID fields.
        normalized = issue
    # Require the exact requested identifier and a canonical immutable UUID.
    if normalized.get("id") != identifier:
        raise core.WorkspaceError("ISSUE_MISMATCH")
    value = normalized.get("uuid")
    # Immutable identity must be supplied as a UUID string.
    if not isinstance(value, str):
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    # Reject malformed and noncanonical UUID spellings without coercion.
    try:
        parsed = uuid.UUID(value)
    # Malformed UUID text cannot authorize a task workspace.
    except ValueError as error:
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID") from error
    # Require canonical UUID text after parsing.
    if str(parsed) != value:
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    return normalized


def ticket(response: object, identifier: str) -> core.JSONObject:
    """Extract the exact requested issue from a successful connector result.

    Args:
        response: Observed connector envelope or normalized Linear issue object.
        identifier: Exact shorthand issue identifier requested by the user.

    Returns:
        An issue with shorthand ``id`` and canonical immutable ``uuid``.

    Raises:
        core.WorkspaceError: The provider failed, confirmed absence, or returned unusable data.
    """
    # Select the connector envelope or an explicit normalized GraphQL issue object.
    if not isinstance(response, dict):
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    # Accept a direct normalized issue object when no connector envelope exists.
    if "isError" not in response and set(response) >= {"id", "identifier"}:
        return _normalize_issue(response, identifier)
    # Fail separately for confirmed absence, provider failure, and malformed output.
    if response.get("isError") is True:
        # Exact provider shapes are needed; generic invalid requests are not absence.
        code = response.get("code")
        # Only explicit not-found codes establish confirmed absence.
        if isinstance(code, str) and code in {"NOT_FOUND", "ISSUE_NOT_FOUND"}:
            raise core.WorkspaceError("ISSUE_NOT_FOUND")
        # Preserve the connector's observed unresolved-reference diagnostic.
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
    # Parse only a single successful text result and validate its issue identity.
    if response.get("isError") is not False:
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    content = response.get("content")
    # A successful connector result has exactly one content block.
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    # Only a text block can hold this supported serialized issue object.
    if content[0].get("type") != "text" or not isinstance(content[0].get("text"), str):
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID")
    # Parse the serialized issue without accepting arbitrary result shapes.
    try:
        issue = json.loads(content[0]["text"])
    # Malformed issue JSON is an invalid provider response.
    except ValueError as error:
        raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID") from error
    return _normalize_issue(issue, identifier)


def _call(request: core.JSONObject) -> core.JSONObject:
    """Require a successful lifecycle result while retaining its exact failure code.

    Args:
        request: Lifecycle operation and explicit host/session identity.

    Returns:
        The successful lifecycle result.

    Raises:
        core.WorkspaceError: If the lifecycle reports a failed operation.
    """
    # Add a fresh request ID and retain the exact core failure code.
    result = core.execute({"schema_version": 1, "request_id": str(uuid.uuid4()), **request})
    # Preserve the lifecycle’s exact failure code.
    if not result["ok"]:
        raise core.WorkspaceError(result["code"])
    return result


def _read_file(path: Path) -> bytes:
    """Read an assigned source through the lifecycle's no-follow filesystem API.

    Args:
        path: Assigned source path under the selected checkout or task store.

    Returns:
        The source's exact bytes.

    Raises:
        core.WorkspaceError: If the no-follow file boundary rejects the source.
    """
    # Read assigned bytes through the no-follow directory boundary.
    with core.Directory.absolute(path.parent) as parent:
        return parent.read(path.name)


def _initial_packet(checkout: Path, main: Path, issue_id: str, reader: str) -> list[core.SourceRef]:
    """Build the packet from canonical task bytes and the selected checkout's rules.

    Args:
        checkout: Actual checkout selected by the host session.
        main: Main worktree containing canonical task files.
        issue_id: Verified shorthand issue identifier.
        reader: Exact participant key receiving the packet.

    Returns:
        Required source references for the initial planning reader.

    Raises:
        core.WorkspaceError: If a required packet source fails the no-follow file boundary.
        OSError: If a source cannot be read from the selected checkout.
    """
    # Select canonical task content and checkout-specific governing files.
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
    # Hash each available source before installing its reader-specific reference.
    for source_id, locator, path in sources:
        # Optional checkout rules may be absent from a task checkout.
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

    Args:
        event: Observed native session and checkout identity.
        issue: Verified issue with shorthand ID and immutable UUID.
        host: Explicit adapter host bound to the session.

    Returns:
        Ready lifecycle result with participant and coordinator identities.

    Raises:
        core.WorkspaceError: If registration, scope, source, or readiness fails.
    """
    # Derive the exact repository and host-bound request from verified inputs.
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
    # Register this checkout idempotently and diagnose existing task state.
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
    # A new issue binds this initiating session as coordinator and installs sources.
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
        # Resume or join without replacing prior coordinator or packet ownership.
        with core.Store(base) as store, store.issues.child(identifier) as control, control.lock():
            current = core.Issue(store, control, identifier)
            current.recover()
            state = current.committed_state()
            # An existing task must match the provider’s immutable issue UUID.
            if state["issue_uuid"] != issue["uuid"]:
                raise core.WorkspaceError("ISSUE_MISMATCH")
            participant = state["participants"].get(key)
            needs_join = (
                key != state["coordinator"]
                and not participant
                and key not in state.get("assignments", {})
            )
            # Carry forward the participant’s current binding generation.
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
        # Reload committed state after join or resume under the issue lock.
        with core.Store(base) as store, store.issues.child(identifier) as control, control.lock():
            current = core.Issue(store, control, identifier)
            current.recover()
            state = current.committed_state()
            current.files()  # Verify the committed manifest before refreshing any packet digest.
            participant = state["participants"][key]
            missing_packet = participant["packet"] is None
        # Repair only a coordinator's missing packet; readers keep their assigned scope.
        if missing_packet:
            # Only the coordinator may install a missing packet.
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
        # A reader’s resume packet may need a refreshed roadmap digest.
        elif key != state["coordinator"] and participant["packet"] is not None:
            packet = participant["packet"]
            # Refresh only the single documented roadmap-only reader packet.
            if (
                len(packet) == 1
                and packet[0]["locator"] == "roadmap.md"
                and packet[0]["reason"] == "issue-resume"
                and packet[0]["sha256"] != state["files"]["roadmap.md"]
            ):
                _call({**base, "operation": "join", "expected_revision": state["revision"]})
        # A coordinator can refresh its owned roadmap reference.
        elif key == state["coordinator"] and state["owners"]["roadmap.md"] == key:
            # A coordinator's normal lifecycle update changes the canonical roadmap
            # digest. Refresh only that owned reference; external edits fail files().
            assert participant["packet"] is not None
            packet = [ref.copy() for ref in participant["packet"]]
            changed = False
            # Inspect each assigned source reference for a changed roadmap digest.
            for ref in packet:
                # Change only the owned roadmap reference when its digest differs.
                if ref["locator"] == "roadmap.md" and ref["sha256"] != state["files"]["roadmap.md"]:
                    ref["sha256"] = state["files"]["roadmap.md"]
                    changed = True
            # Persist a refreshed packet only when an owned digest changed.
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
    # Read every assigned source byte and acknowledge the exact packet digest.
    read = _call({**base, "operation": "read"})
    # A descriptor alone is not a delivered source. Read and hash each assigned file.
    for ref in read["references"]:
        # Skip optional references absent from the delivered packet.
        if not ref["available"]:
            continue
        locator = Path(ref["locator"])
        path = locator if locator.is_absolute() else trees[0] / ".task" / identifier / locator
        # Reject assigned source bytes that no longer match their digest.
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
    # Verify readiness and return the preserved coordinator identity.
    ready = _call({**base, "operation": "ready"})
    # Read the preserved coordinator from the committed issue state.
    with core.Store(base) as store, store.issues.child(identifier) as control, control.lock():
        current = core.Issue(store, control, identifier)
        current.recover()
        ready["coordinator"] = current.committed_state()["coordinator"]
    ready["participant_id"] = key
    return ready
