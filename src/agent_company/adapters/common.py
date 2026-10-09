"""Share host-bound assignment and exact bootstrap checks across native adapters."""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from agent_company.adapters import bootstrap as encoded_bootstrap
from agent_company.adapters import runner
from agent_company.lifecycle import task_workspace as core

ROOT = Path(__file__).resolve().parents[3]

BOOTSTRAP_FIELDS = {
    "diagnose": set(),
    "register": {"main_worktree", "startup"},
    "bind": set(),
    "adopt": {"coordinator", "inventory", "owners", "evidence"},
    "resume": set(),
    "restore": {"observations"},
    "read": set(),
    "acknowledge": {"packet_digest"},
    "archive-index": {"observations"},
    "archive-verify": {"observations"},
    "archive-observe-save": {"document_id", "content_digest"},
    "cleanup-plan": set(),
    "cleanup-commit": {"observations", "cleanup_challenge"},
    "reopen": {"evidence"},
    "archive-prepare": {"seal"},
    "event-rollover": set(),
    "reconcile-files": {"inventory", "evidence"},
    "rebind": {"new_issue_id", "new_binding_generation", "evidence"},
}
COMMON_FIELDS = {
    "schema_version",
    "operation",
    "request_id",
    "worktree",
    "host",
    "session_id",
    "repo_id",
    "issue_id",
    "issue_uuid",
    "binding_generation",
    "expected_revision",
}
LIFECYCLE = ROOT / "src/agent_company/lifecycle/task_workspace.py"
WINDOWS_BOOTSTRAP = ROOT / "src/agent_company/adapters/bootstrap.py"
PYTHON = str(ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def request_for(event: core.JSONObject, operation: str, host: str) -> core.JSONObject:
    """Resolve an actual hook session to its registered repository and binding.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        host: Explicit adapter identity; never taken from untrusted tool arguments.
        operation: Core operation requested for the observed session.

    Returns:
        A versioned core request carrying the observed session identity.

    Raises:
        core.WorkspaceError: If registration or binding validation fails.
        OSError: If the local store cannot be read.
    """
    # Build the request from explicit caller or observed session identities.
    request = {
        "schema_version": 1,
        "operation": operation,
        "request_id": str(uuid.uuid4()),
        "worktree": event["cwd"],
        "host": host,
        "session_id": event["session_id"],
    }
    diagnosis = core.execute({**request, "operation": "diagnose"})
    # Stop on the exact repository diagnostic before opening a bound session.
    if not diagnosis["ok"] or diagnosis["code"] != "REGISTERED":
        raise core.WorkspaceError(diagnosis["code"])
    # Carry the verified repository identity into the lifecycle request.
    request["repo_id"] = diagnosis["repo_id"]
    # Open the verified store only after registration succeeds.
    with core.Store(request) as store:
        # Read the existing session binding; never infer it from the prompt.
        binding = store.binding()
        # Leave an absent assignment unbound rather than inventing identity.
        if not binding:
            raise core.WorkspaceError("BINDING_MISSING")
        # Copy only the recorded issue and generation into the request.
        request.update(binding)
    return request


def automatic_attach(event: core.JSONObject, identifier: str, host: str) -> None:
    """Apply only an explicit startup assignment or an existing participant packet.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        host: Explicit adapter identity; never taken from untrusted tool arguments.
        identifier: Validated issue ID or explicit tool-call identity.

    Raises:
        core.WorkspaceError: If registration, assignment or lifecycle setup fails.
        OSError: If assignment or issue state cannot be accessed.
    """
    # Build the request from explicit caller or observed session identities.
    base = {
        "schema_version": 1,
        "request_id": "startup:" + event["session_id"],
        "operation": "diagnose",
        "worktree": event["cwd"],
        "host": host,
        "session_id": event["session_id"],
    }
    diagnosis = core.execute(base)
    # Only a registered checkout can carry a startup assignment.
    if diagnosis.get("code") != "REGISTERED":
        return
    base.update(repo_id=diagnosis["repo_id"], issue_id=identifier)
    # Inspect the persisted binding and startup packet under the registered store.
    with core.Store(base) as store:
        # Read the existing session binding; never infer it from the prompt.
        binding = store.binding()
        # An existing binding must match the submitted Task identity.
        if binding:
            core.require(binding["issue_id"] == identifier, "BINDING_CONFLICT")
        key = core.participant_key(base)
        filename = key + ".startup.json"
        setup = store.bindings.json(filename) if store.bindings.exists(filename) else None
        # A bound participant may already have a committed packet.
        if binding:
            # Read issue state under its lock before deciding whether setup is needed.
            with store.issues.child(identifier) as control, control.lock():
                issue = core.Issue(store, control, identifier)
                issue.recover()
                member: Mapping[str, object] = issue.committed_state()["participants"].get(key, {})
                # Use only the assigned packet and its current acknowledgment.
                if member.get("packet") is not None:
                    return
    # Create only the explicitly assigned startup issue before applying its packet.
    if setup:
        core.require(setup["issue_id"] == identifier, "BINDING_CONFLICT")
        result = core.execute(
            {
                **base,
                "operation": "create",
                "coordinator": setup["coordinator"],
                "issue_uuid": setup["issue_uuid"],
            }
        )
        core.require(result["ok"], result["code"])
        # A previous scope failure may follow successful creation. Read current revision
        # and retry only the still-missing assignment, never replace a later packet.
        diagnostic = core.execute(
            {**base, "operation": "diagnose", "request_id": base["request_id"] + ":diagnose"}
        )
        result = core.execute(
            {
                **base,
                "operation": "scope",
                "request_id": base["request_id"] + ":scope",
                "binding_generation": result["binding_generation"],
                "expected_revision": diagnostic["revision"],
                "target_participant": key,
                "packet": setup["packet"],
            }
        )
        core.require(result["ok"], result["code"])
    # Leave an absent assignment unbound rather than inventing identity.
    elif not binding:
        # Existing coordinator assignment in shared state is required for a new join.
        result = core.execute({**base, "operation": "attach"})
        # Preserve lifecycle failures other than the documented unassigned states.
        if not result["ok"] and result["code"] not in {"BINDING_MISSING", "SCOPE_MISSING"}:
            raise core.WorkspaceError(result["code"])


def prompt(event: core.JSONObject, host: str, attempt_attach: bool = False) -> core.JSONObject:
    """Record one explicit Task line and attempt only its assigned workspace setup.

    Args:
        event: Observed host hook input, including the actual session and tool identities.
        host: Explicit adapter identity; never taken from untrusted tool arguments.
        attempt_attach: Whether to use the legacy preassigned setup after recording Task.

    Returns:
        An empty response, prompt denial or bounded setup context.

    Raises:
        core.WorkspaceError: If workspace registration or setup fails.
        OSError: If assignment persistence fails.
    """
    # Extract only explicit Task lines from the submitted prompt.
    lines = [line for line in event.get("prompt", "").splitlines() if line.startswith("Task:")]
    # Leave ordinary prompts unchanged when no task identity was supplied.
    if not lines:
        return {}
    # Reject multiple or malformed Task lines before recording an assignment.
    if len(lines) != 1 or not re.fullmatch(r"Task: [A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,9}", lines[0]):
        return {
            "decision": "block",
            "reason": "BINDING_CONFLICT: Supply exactly one Task: ISSUE-ID line.",
        }
    # Build the request from explicit caller or observed session identities.
    identifier = lines[0][6:]
    request = {"host": host, "session_id": event["session_id"]}
    root, _, _ = core.repository(event["cwd"])
    # Lock assignment storage before recording or comparing Task identity.
    with (
        core.Directory.absolute(root) as worktree,
        worktree.child(".task", True) as local,
        local.child(".bindings", True) as bindings,
        bindings.lock("assignment.lock"),
    ):
        # Derive the assignment key from the explicit host/session identity.
        key = core.participant_key(request)
        # Reject implicit task switches in either the live binding or the recorded assignment.
        for name in (key + ".json", key + ".assignment.json"):
            # Validate any existing entry before reusing or replacing it.
            if bindings.exists(name) and bindings.json(name)["issue_id"] != identifier:
                return {
                    "decision": "block",
                    "reason": "BINDING_CONFLICT: Explicit rebind is required.",
                }
        bindings.put(key + ".assignment.json", {"issue_id": identifier})
        # Ticket-first hosts retain a marker until the provider read completes.
        if not attempt_attach:
            bindings.put(key + ".lookup-required.json", {"issue_id": identifier})
    # Attempt setup using only the recorded startup assignment or existing packet.
    if attempt_attach:
        automatic_attach(event, identifier, host)
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "Task identity recorded. Read the requested Linear ticket first. "
                "Then complete scoped workspace setup and verify readiness."
                if not attempt_attach
                else "Task identity recorded. Explicitly assigned workspace setup was "
                "attempted. Read and acknowledge the permitted packet before task "
                "tools; use the lifecycle diagnostic route if setup is missing."
            ),
        }
    }


def lookup_required(event: core.JSONObject, host: str) -> bool:
    """Check whether this exact native session awaits its first Linear read.

    Args:
        event: Identity-validated host event.
        host: Native host identity.

    Returns:
        Whether an exact ticket lookup marker exists for the session.

    Raises:
        core.WorkspaceError: If the selected checkout or binding path is invalid.
        OSError: If the task store cannot be read.
    """
    # Synthetic contexts without a native identity have no session assignment.
    if not isinstance(event.get("cwd"), str) or not isinstance(event.get("session_id"), str):
        return False
    # Resolve the selected checkout before looking for a local assignment.
    root, _, _ = core.repository(event["cwd"])
    key = core.participant_key({"host": host, "session_id": event["session_id"]})
    # Inspect the root store without creating a ticket or binding.
    with core.Directory.absolute(root) as directory:
        # A missing store means no ticket-first assignment is active.
        if not directory.exists(".task"):
            return False
        # An uninitialized binding store cannot contain a pending ticket.
        with directory.child(".task") as task:
            # Stop before opening an absent bindings directory.
            if not task.exists(".bindings"):
                return False
            # Read only the marker owned by this native session.
            with task.child(".bindings") as bindings:
                return bindings.exists(key + ".lookup-required.json")


def _linear_tool(event: core.JSONObject, host: str) -> tuple[str, core.JSONObject] | None:
    """Validate one host's documented Linear MCP name and argument shape.

    Args:
        event: Native tool event with host-specific fields.
        host: Either Claude Code or Cursor.

    Returns:
        A correlation token and exact input, or None for another tool.

    Raises:
        core.WorkspaceError: If a candidate tool has malformed input or provenance.
    """
    # Claude reports an MCP-qualified name and a server provenance object.
    if host == "claude-code":
        server = event.get("mcp_server")
        name = event.get("tool_name")
        # Ignore unrelated Claude tools before accessing provider metadata.
        if name not in {"mcp__linear__get_issue", "mcp__linear-server__get_issue"}:
            return None
        core.require(
            isinstance(server, dict)
            and server.get("name") in {"linear", "linear-server"}
            and server.get("source") in {"user", "project", "plugin", "sdk"}
            and name == "mcp__" + server["name"] + "__get_issue",
            "HOST_UNSUPPORTED_PROVIDER",
        )
        arguments = event.get("tool_input")
        token = native_token(event.get("tool_use_id"))
    else:
        # Cursor's MCP-specific callbacks identify the configured server and carry JSON strings.
        # Ignore unrelated Cursor MCP tools before validating server identity.
        if event.get("tool_name") != "get_issue":
            return None
        core.require(
            event.get("mcp_server_name") in {"linear", "linear-server"}, "HOST_UNSUPPORTED_PROVIDER"
        )
        url = event.get("mcp_server_url", event.get("url"))
        core.require(
            url in {"https://mcp.linear.app/mcp", "https://mcp.linear.app/mcp/readonly"},
            "HOST_UNSUPPORTED_PROVIDER",
        )
        raw = event.get("tool_input")
        # Decode the documented JSON argument string, never a shell command.
        if not isinstance(raw, str):
            raise core.WorkspaceError("INVALID_REQUEST")
        arguments = core.strict_json(raw)
        token = "cursor-mcp-server-checked"
    # The requested issue identifier must come from an object argument.
    if not isinstance(arguments, dict):
        raise core.WorkspaceError("INVALID_REQUEST")
    return token, arguments


def native_ticket_lookup(
    event: core.JSONObject, host: str, complete: bool = False, specific: bool = False
) -> core.JSONObject | None:
    """Admit and settle the first exact Linear read before lifecycle readiness.

    Args:
        event: Identity-validated Claude tool or Cursor MCP callback.
        host: Native host identity.
        complete: Whether the provider has returned its result.
        specific: Whether Cursor supplied its server-specific MCP callback.

    Returns:
        None for another tool, otherwise host-neutral ticket startup context.

    Raises:
        core.WorkspaceError: If assignment, provider result, or startup fails.
        OSError: If the assignment store or packet source cannot be accessed.
    """
    # Cursor's generic callback supplies the native tool ID. Its MCP-specific
    # callback supplies server identity; both must occur before completion.
    selected: tuple[str, core.JSONObject] | None
    # Select Cursor's native call ID from generic pre/post callbacks.
    if host == "cursor" and not specific:
        # Ignore unrelated generic tool events.
        if event.get("tool_name") != "MCP:get_issue":
            return None
        arguments = event.get("tool_input")
        token = native_token(event.get("tool_use_id"))
        # Reject malformed generic MCP arguments before opening state.
        if not isinstance(arguments, dict):
            raise core.WorkspaceError("INVALID_REQUEST")
        selected = token, arguments
    else:
        # Claude or Cursor's MCP-specific callback has its own provider shape.
        selected = _linear_tool(event, host)
    # A different tool cannot use the ticket-first exception.
    if selected is None:
        return None
    token, arguments = selected
    root, _, _ = core.repository(event["cwd"])
    key = core.participant_key({"host": host, "session_id": event["session_id"]})
    # Lock the session assignment while admitting or settling one provider call.
    with (
        core.Directory.absolute(root) as directory,
        directory.child(".task") as task,
        task.child(".bindings") as bindings,
        bindings.lock("assignment.lock"),
    ):
        # Correlate the exact Task identifier and one in-flight provider lookup.
        assignment = bindings.json(key + ".assignment.json")
        identifier = assignment["issue_id"]
        core.require(bindings.json(key + ".lookup-required.json") == assignment, "BINDING_CONFLICT")
        core.require(arguments == {"id": identifier}, "BINDING_CONFLICT")
        name = key + ".lookup.json"
        correlation = {"id": identifier, "tool_id": token}
        # Cursor requires both its generic call ID and specific server check.
        if host == "cursor":
            correlation = {
                "id": identifier,
                "tool_id": None if specific else token,
                "generic": not specific,
                "specific": specific,
            }
        # Pre-hooks reserve one exact call before its provider result can settle.
        if not complete:
            # Reuse only the matching in-flight marker or a permitted retry.
            if bindings.exists(name):
                previous = bindings.json(name)
                # Cursor merges distinct prehook facts without admitting overlap.
                if host == "cursor":
                    core.require(
                        previous.get("id") == identifier
                        and not previous["specific" if specific else "generic"]
                        and (specific or previous.get("tool_id") in {None, token}),
                        "PROVIDER_LOOKUP_IN_FLIGHT",
                    )
                    previous.update(
                        generic=previous["generic"] or not specific,
                        specific=previous["specific"] or specific,
                        tool_id=previous["tool_id"] if specific else token,
                    )
                    bindings.put(name, previous)
                else:
                    # Claude retains its native call ID through same-result retries.
                    core.require(
                        previous == correlation
                        or previous == {**correlation, "completed": True}
                        or (
                            isinstance(previous, dict)
                            and set(previous) == {"id", "tool_id", "completed"}
                            and previous["id"] == identifier
                            and previous["completed"] is True
                            and isinstance(previous["tool_id"], str)
                        ),
                        "BINDING_CONFLICT",
                    )
                    # A new Claude call ID may replace only a completed failure.
                    if previous.get("completed") is True and previous.get("tool_id") != token:
                        bindings.put(name, correlation)
            else:
                # First prehook creates the call marker under the assignment lock.
                bindings.put(name, correlation)
            return {}
        core.require(bindings.exists(name), "BINDING_MISSING")
        attempt = bindings.json(name)
        # Completion must prove both Cursor prehook facts and the native call ID.
        if host == "cursor":
            core.require(
                attempt.get("id") == identifier
                and attempt.get("tool_id") == token
                and attempt.get("generic") is True
                and attempt.get("specific") is True,
                "BINDING_CONFLICT",
            )
        else:
            # Claude completion must match its exact admitted native call ID.
            core.require(
                attempt in (correlation, {**correlation, "completed": True}), "BINDING_CONFLICT"
            )
    # Validate the provider result before any issue workspace registration.
    try:
        # Decode only the documented result field for this host.
        response = event.get("tool_response") if host == "claude-code" else event.get("tool_output")
        # Native hooks may serialize their result as a JSON string.
        if isinstance(response, str):
            # Malformed provider JSON is a provider response failure.
            try:
                response = json.loads(response)
            # Report malformed serialized provider results as response errors.
            except ValueError as error:
                # Preserve malformed-data identity instead of calling it missing.
                raise core.WorkspaceError("PROVIDER_RESPONSE_INVALID") from error
        from agent_company.adapters import startup

        verified = startup.ticket(response, identifier)
    # Remove only this attempt when provider validation fails.
    except core.WorkspaceError:
        # A failed provider read permits a fresh exact retry without creating a workspace.
        with core.Directory.absolute(root) as directory, directory.child(".task") as task:
            # Remove only this failed attempt if no newer call replaced it.
            with task.child(".bindings") as bindings, bindings.lock("assignment.lock"):
                # Another call's marker must survive this late failure.
                if bindings.json(name) == attempt:
                    bindings.unlink(name)
        raise
    # Register or resume only after issue ID and immutable UUID validation.
    try:
        # The verified UUID now authorizes idempotent local registration and packet readiness.
        ready = startup.start(event, verified, host=host)
    # Release or retain correlation according to the host retry contract.
    except Exception:
        # Cursor can identify a fresh call only through its generic pre-hook.
        # Clear its completed attempt so either pre-hook order can start again.
        # Claude retains a completed native ID for exact-result retries.
        with core.Directory.absolute(root) as directory, directory.child(".task") as task:
            # Mark or clear only the attempt whose setup just failed.
            with task.child(".bindings") as bindings, bindings.lock("assignment.lock"):
                # Preserve a replacement call that raced with this failure.
                if bindings.json(name) == attempt:
                    # Cursor starts fresh because its specific hook has no call ID.
                    if host == "cursor":
                        bindings.unlink(name)
                    else:
                        # Claude can retry the same result under its exact native ID.
                        bindings.put(name, {**attempt, "completed": True})
        raise
    # Clear only the completed attempt; another attempt must retain its own marker.
    with core.Directory.absolute(root) as directory, directory.child(".task") as task:
        # Verify no newer lookup replaced the successful attempt.
        with task.child(".bindings") as bindings, bindings.lock("assignment.lock"):
            core.require(
                bindings.json(name) in (attempt, {**attempt, "completed": True}), "BINDING_CONFLICT"
            )
            bindings.unlink(name)
            bindings.unlink(key + ".lookup-required.json")
    return {"issue_id": identifier, "reader": ready["participant_id"] != ready["coordinator"]}


def native_ticket_failed(event: core.JSONObject, host: str) -> None:
    """Clear a failed native provider attempt while retaining its Task assignment.

    Args:
        event: Host failure callback for the pending Linear read.
        host: Native host identity.

    Raises:
        core.WorkspaceError: If the failed call does not match the pending attempt.
    """
    # Confirm the failed generic call's native ID and exact arguments. The
    # server-specific pre-hook must already have validated the same single call.
    selected: tuple[str, core.JSONObject] | None
    # Cursor failures carry a native ID in their generic failure callback.
    if host == "cursor":
        core.require(event.get("tool_name") == "MCP:get_issue", "BINDING_CONFLICT")
        arguments = event.get("tool_input")
        token = native_token(event.get("tool_use_id"))
        # Reject malformed failure arguments before altering the pending marker.
        if not isinstance(arguments, dict):
            raise core.WorkspaceError("INVALID_REQUEST")
        selected = token, arguments
    else:
        # Claude failures retain the same MCP-qualified native tool identity.
        selected = _linear_tool(event, host)
        # Unrelated failures cannot clear the Linear lookup marker.
        if selected is None:
            raise core.WorkspaceError("BINDING_CONFLICT")
        token, arguments = selected
    root, _, _ = core.repository(event["cwd"])
    key = core.participant_key({"host": host, "session_id": event["session_id"]})
    # Remove only the matching native failure under the assignment lock.
    with core.Directory.absolute(root) as directory, directory.child(".task") as task:
        # Validate Task identity before reading the attempt marker.
        with task.child(".bindings") as bindings, bindings.lock("assignment.lock"):
            assignment = bindings.json(key + ".assignment.json")
            core.require(
                bindings.json(key + ".lookup-required.json") == assignment, "BINDING_CONFLICT"
            )
            name = key + ".lookup.json"
            current = bindings.json(name)
            core.require(arguments == {"id": assignment["issue_id"]}, "BINDING_CONFLICT")
            # Cursor must match the generic native tool ID already recorded.
            if host == "cursor":
                core.require(
                    current.get("id") == assignment["issue_id"]
                    and current.get("tool_id") == token
                    and current.get("generic") is True,
                    "BINDING_CONFLICT",
                )
            else:
                # Claude must match its exact MCP call correlation.
                core.require(
                    current == {"id": assignment["issue_id"], "tool_id": token}, "BINDING_CONFLICT"
                )
            bindings.unlink(name)


def bootstrap_command(request: core.JSONObject, host: str) -> str:
    """Format the sole bootstrap spelling for this platform and host shell.

    Args:
        request: Explicit lifecycle request with host and session identities.
        host: Native adapter identity, determining Bash versus PowerShell syntax.

    Returns:
        A canonical command containing no expandable shell arguments.

    Raises:
        ValueError: If an argument contains shell-control or smart-quote characters.
    """
    # Use POSIX quoting for Unix or Claude Bash invocation.
    if os.name != "nt" or host == "claude-code":
        command = shlex.join([PYTHON, str(LIFECYCLE), "--request-json", json.dumps(request)])
    else:
        # Windows Cursor receives a base64 payload with fixed PowerShell quoting.
        encoded = base64.urlsafe_b64encode(json.dumps(request).encode()).decode("ascii")
        argv = [PYTHON, str(WINDOWS_BOOTSTRAP), "--request-base64", encoded]
        # Reject characters that can escape canonical Windows quoting.
        if any(any(c in arg for c in '\r\n\0"‘’“”') for arg in argv):
            raise ValueError("Unsupported PowerShell argument")
        command = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
    # Bound the shell command size and reject control characters.
    if len(command.encode()) > 65536 or any(c in command for c in "\r\n\0"):
        raise ValueError("Unsupported bootstrap command size or control character")
    return command


def bootstrap_request(command: str, host: str, python: str, lifecycle: Path) -> core.JSONObject:
    """Parse only the exact literal invocation for the selected platform shell.

    Args:
        command: Observed tool command, never evaluated.
        host: Native adapter identity.
        python: Exact checkout interpreter.
        lifecycle: Exact POSIX lifecycle entry point.

    Returns:
        The decoded request after canonical argument checks.

    Raises:
        ValueError: If shell syntax is not canonical.
        core.WorkspaceError: If the encoded request is malformed.
    """
    # Reject control characters before parsing either shell syntax.
    if any(character in command for character in "\r\n\0"):
        raise ValueError("Control character in command")
    # Parse the exact Windows PowerShell spelling for Cursor.
    if os.name == "nt" and host != "claude-code":
        token = r"'(?:[^'\r\n\x00]|'')*'"
        # Require four quoted PowerShell tokens with no extra expression.
        if not re.fullmatch(r"& " + " ".join([token] * 4), command):
            raise ValueError("Noncanonical PowerShell command")
        argv = [match[1:-1].replace("''", "'") for match in re.findall(token, command[2:])]
        # Reject unsupported quoting even after tokenization.
        if any(any(c in arg for c in '"‘’“”') for arg in argv):
            raise ValueError("Unsupported PowerShell quoting")
        # Bind Windows commands to this checkout interpreter and entry point.
        if argv[:3] != [python, str(WINDOWS_BOOTSTRAP), "--request-base64"]:
            raise ValueError("Unexpected Windows bootstrap entry")
        return encoded_bootstrap.decode_request(argv[3])
    argv = shlex.split(command)
    # Bind POSIX commands to this checkout interpreter and entry point.
    if len(argv) != 4 or argv[:3] != [python, str(lifecycle), "--request-json"]:
        raise ValueError("Unexpected bootstrap entry")
    # Require the command to round-trip to its canonical spelling.
    if command != shlex.join(argv):
        raise ValueError("Noncanonical shell command")
    request = core.strict_json(argv[3])
    # The lifecycle payload must be a JSON object.
    if not isinstance(request, dict):
        raise ValueError("Invalid request object")
    return request


def canonical_bootstrap(
    event: core.JSONObject,
    command: object,
    host: str,
    ready: bool,
    python: str = PYTHON,
    lifecycle: Path = LIFECYCLE,
) -> bool:
    """Recognize one canonical lifecycle invocation bound to this host and session.

    Args:
        event: Observed session and worktree identities.
        command: Native shell command value to inspect without executing it.
        host: Explicit adapter identity.
        ready: Whether ordinary readiness was already established.
        python: Exact project interpreter accepted by this adapter.
        lifecycle: Exact lifecycle entry point accepted by this adapter.

    Returns:
        Whether the command satisfies the exact identity and recovery schema.
    """
    # Reject oversized or nontext shell commands before parsing.
    if not isinstance(command, str) or len(command.encode()) > 65536:
        return False
    # Contain malformed commands as noncanonical observations.
    try:
        request = bootstrap_request(command, host, python, lifecycle)
        # Select the operation-specific bootstrap field policy.
        operation = request.get("operation")
        # Limit unready sessions to the documented recovery operation schemas.
        if not ready and (
            operation not in BOOTSTRAP_FIELDS
            or not set(request) <= COMMON_FIELDS | BOOTSTRAP_FIELDS[operation]
        ):
            return False
        # Bind bootstrap execution to the actual hook session and host.
        if request.get("session_id") != event["session_id"] or request.get("host", "codex") != host:
            return False
        # Require the bootstrap worktree to match the observed hook worktree.
        if core.repository(request["worktree"])[0] != core.repository(event["cwd"])[0]:
            return False
        # Keep registration and diagnostics free of arbitrary issue/tool fields.
        if request["operation"] in {"register", "diagnose"}:
            return set(request) <= {
                "schema_version",
                "operation",
                "request_id",
                "worktree",
                "host",
                "session_id",
                "main_worktree",
                "startup",
            }
        # Require the bootstrap issue to match the session assignment stored in this worktree.
        with (
            core.Directory.absolute(core.repository(event["cwd"])[0]) as root,
            root.child(".task") as local,
        ):
            # Read the recorded assignment through the local binding directory.
            with local.child(".bindings") as bindings:
                assignment = bindings.json(core.participant_key(request) + ".assignment.json")
        matches_assignment: bool = request.get("issue_id") == assignment["issue_id"]
        return matches_assignment
    # Malformed or inaccessible assignment state never authorizes bootstrap.
    except (core.WorkspaceError, OSError, ValueError, KeyError, TypeError):
        return False


def native_token(value: object) -> str:
    """Validate a native identity without coercing absent or malformed values.

    Args:
        value: Native session, event, or tool identifier.

    Returns:
        The validated identity token.

    Raises:
        core.WorkspaceError: If the value is not a bounded string token.
    """
    # Do not coerce missing or nontext native identities.
    if not isinstance(value, str):
        raise core.WorkspaceError("INVALID_REQUEST")
    return core.token(value)


def native_identity(event: core.JSONObject, host: str) -> core.JSONObject:
    """Validate native session and worktree identity without manufacturing either.

    Args:
        event: Native host hook envelope.
        host: Either claude-code or cursor, selected by the entry point.

    Returns:
        A normalized envelope retaining native tool IDs and inputs.

    Raises:
        core.WorkspaceError: If identity is absent, ambiguous, or belongs to a child.
    """
    # Children and remote/background sessions have no supported binding here.
    core.require(
        event.get("agent_id") is None
        and event.get("subagent_id") is None
        and event.get("parent_conversation_id") is None,
        "HOST_UNSUPPORTED_CHILD_IDENTITY",
    )
    core.require(event.get("is_background_agent", False) is False, "HOST_UNSUPPORTED_BACKGROUND")
    normalized = event.copy()
    # Cursor identity comes from its conversation and single workspace root.
    if host == "cursor":
        session = native_token(event.get("conversation_id"))
        core.require(event.get("session_id", session) == session, "BINDING_CONFLICT")
        roots = event.get("workspace_roots")
        # Reject ambiguous or malformed Cursor workspace roots.
        if not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], str):
            raise core.WorkspaceError("HOST_UNSUPPORTED_WORKSPACE")
        cwd = event.get("cwd", roots[0])
        core.require(
            isinstance(cwd, str) and core.repository(cwd)[0] == core.repository(roots[0])[0],
            "REPOSITORY_MISMATCH",
        )
    else:
        # Claude binds to its native session and absolute working directory.
        session = native_token(event.get("session_id"))
        cwd = event.get("cwd")
        core.require(isinstance(cwd, str) and Path(cwd).is_absolute(), "REPOSITORY_MISMATCH")
    normalized.update(session_id=session, cwd=cwd)
    return normalized


def native_bootstrap(event: core.JSONObject, host: str, ready: bool = False) -> bool:
    """Check native shell arguments before the shared canonical command parser.

    Args:
        event: Identity-validated native envelope.
        host: Explicit adapter identity.
        ready: Whether the core already established readiness.

    Returns:
        Whether this exact native tool call is a scoped lifecycle invocation.
    """
    # Native shells do not expose Codex's login/shell fields; accept only documented inputs.
    shell = "Bash" if host == "claude-code" else "Shell"
    # Only the host’s native shell tool can execute a bootstrap command.
    if event.get("tool_name") != shell:
        return False
    args = event.get("tool_input")
    allowed = (
        {"command", "description", "timeout", "run_in_background"}
        if host == "claude-code"
        else {"command", "working_directory"}
    )
    # Reject undocumented native shell arguments.
    if not isinstance(args, dict) or not set(args) <= allowed:
        return False
    # Disallow background bootstrap commands.
    if args.get("run_in_background", False) is not False:
        return False
    # A native working-directory override cannot redirect the bootstrap to another repository.
    try:
        # Reject working-directory redirects to another repository.
        if "working_directory" in args and (
            not isinstance(args["working_directory"], str)
            or core.repository(args["working_directory"])[0] != core.repository(event["cwd"])[0]
        ):
            return False
        return canonical_bootstrap(event, args.get("command"), host, ready)
    # Unverifiable shell details cannot establish canonical bootstrap.
    except (core.WorkspaceError, OSError, ValueError, KeyError, TypeError):
        return False


CLAUDE_SYNC_TOOLS = frozenset({"Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit"})
CURSOR_SYNC_TOOLS = frozenset({"Read", "Write", "Edit", "Grep", "Delete"})


def native_tool(event: core.JSONObject, host: str) -> str:
    """Limit admission to tools whose synchronous completion can be correlated.

    Args:
        event: Native envelope carrying the actual tool-use identifier.
        host: Explicit adapter identity.

    Returns:
        The native tool name after validating its supported input shape.

    Raises:
        core.WorkspaceError: If the tool, child, provider, or async path is unsupported.
    """
    # Tool identity comes only from the host, never from a generated request UUID.
    native_token(event.get("tool_use_id"))
    tool = native_token(event.get("tool_name"))
    args = event.get("tool_input")
    # Require a structured native tool input for supported synchronous work.
    if not isinstance(args, dict):
        raise core.WorkspaceError("INVALID_REQUEST")
    # Provider calls use the separate ticket-read admission path.
    if tool.startswith(("mcp__", "MCP:")) or event.get("mcp_server") is not None:
        raise core.WorkspaceError("HOST_UNSUPPORTED_PROVIDER")
    # Child and delegation tools require their own verified identity.
    if tool in {"Agent", "Task", "TaskOutput", "TaskStop", "SpawnAgent"}:
        raise core.WorkspaceError("HOST_UNSUPPORTED_CHILD_IDENTITY")
    # The project disables Claude auto-backgrounding; never assume an unconfigured host did so.
    if host == "claude-code" and tool == "Bash":
        core.require(
            os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") == "1", "HOST_UNSUPPORTED_ASYNC"
        )
        core.require(
            set(args) <= {"command", "description", "timeout", "run_in_background"}
            and isinstance(args.get("command"), str)
            and args.get("run_in_background", False) is False,
            "HOST_UNSUPPORTED_ASYNC",
        )
        return tool
    # Cursor Shell needs a bounded synchronous command.
    if host == "cursor" and tool == "Shell":
        core.require(
            set(args) <= {"command", "working_directory"} and isinstance(args.get("command"), str),
            "HOST_UNSUPPORTED_ASYNC",
        )
        # A shell working-directory override must remain in the checkout.
        if "working_directory" in args:
            core.require(
                core.repository(args["working_directory"])[0] == core.repository(event["cwd"])[0],
                "REPOSITORY_MISMATCH",
            )
        return tool
    supported = CLAUDE_SYNC_TOOLS if host == "claude-code" else CURSOR_SYNC_TOOLS
    core.require(tool in supported, "HOST_UNSUPPORTED_TOOL")
    core.require(
        not any(
            key in args for key in ("run_in_background", "background", "is_background", "async")
        ),
        "HOST_UNSUPPORTED_ASYNC",
    )
    return tool


def native_pre(event: core.JSONObject, host: str) -> None:
    """Enforce readiness and record supported native tool admission.

    Args:
        event: Identity-validated native envelope.
        host: Explicit adapter identity.

    Raises:
        core.WorkspaceError: If recovery scope, readiness, or admission fails.
    """
    # Recovery accepts only the assigned exact command before ordinary readiness.
    native_token(event.get("tool_use_id"))
    # An exact lifecycle recovery command is allowed before readiness.
    if native_bootstrap(event, host):
        return
    request = request_for(event, "ready", host)
    result = core.execute(request)
    core.require(result["ok"], result["code"])
    # After readiness, exact lifecycle commands remain distinct from work tools.
    if native_bootstrap(event, host, ready=True):
        return
    # Native permission approval remains separate from recording pending work.
    native_tool(event, host)
    result = core.execute(
        {
            **request,
            "operation": "tool-start",
            "request_id": "pre:" + event["tool_use_id"],
            "tool_id": event["tool_use_id"],
        }
    )
    core.require(result["ok"], result["code"])


def native_post(event: core.JSONObject, host: str, failed: bool) -> None:
    """Settle only a documented synchronous result for the observed native tool ID.

    Args:
        event: Identity-validated success or failure envelope.
        host: Explicit adapter identity.
        failed: Whether this is the host's failure event.

    Raises:
        core.WorkspaceError: If completion is ambiguous or core correlation fails.
    """
    # Lifecycle calls own their transaction and are never admitted as external work.
    if native_bootstrap(event, host, ready=True):
        return
    tool = native_tool(event, host)
    # Require the documented success/failure payload before treating an event as completion.
    if failed:
        field = "error" if host == "claude-code" else "error_message"
        core.require(isinstance(event.get(field), str), "INVALID_REQUEST")
        core.require(type(event.get("is_interrupt", False)) is bool, "INVALID_REQUEST")
        # Cursor failures require the native failure type.
        if host == "cursor":
            core.require(
                event.get("failure_type") in {"error", "timeout", "permission_denied"},
                "INVALID_REQUEST",
            )
    # Cursor success requires its native serialized output field.
    elif host == "cursor":
        core.require(isinstance(event.get("tool_output"), str), "INVALID_REQUEST")
    else:
        # Claude success requires a nonempty native tool response.
        core.require(event.get("tool_response") is not None, "INVALID_REQUEST")
    # Claude Bash completion must prove a foreground terminal result.
    if host == "claude-code" and tool == "Bash":
        core.require(not failed, "HOST_UNSUPPORTED_ASYNC")
        output = event["tool_response"]
        core.require(
            isinstance(output, dict)
            and output.get("interrupted") is False
            and isinstance(output.get("stdout"), str)
            and isinstance(output.get("stderr"), str)
            and not any(
                key in output
                for key in (
                    "backgroundTaskId",
                    "task_id",
                    "session_id",
                    "sessionId",
                    "background",
                    "run_in_background",
                    "is_background",
                    "async",
                )
            ),
            "HOST_UNSUPPORTED_ASYNC",
        )
    # Cursor Shell completion must prove an exit code with no async marker.
    if host == "cursor" and tool == "Shell":
        # A failure/timeout notification does not prove the spawned process terminated.
        core.require(not failed, "HOST_UNSUPPORTED_ASYNC")
        output = core.strict_json(event.get("tool_output", ""))
        core.require(
            isinstance(output, dict)
            and type(output.get("exitCode")) is int
            and not any(
                key in output
                for key in (
                    "session_id",
                    "sessionId",
                    "task_id",
                    "backgroundTaskId",
                    "background",
                    "run_in_background",
                    "is_background",
                    "async",
                )
            ),
            "HOST_UNSUPPORTED_ASYNC",
        )
    # Success/failure hook events settle the supported synchronous file tools themselves.
    request = request_for(event, "tool-complete", host)
    result = core.execute(
        {
            **request,
            "request_id": "native-post:" + core.sha(core.canonical([event["tool_use_id"], failed])),
            "tool_id": event["tool_use_id"],
            "completed": True,
        }
    )
    core.require(result["ok"], result["code"])


def native_context(event: core.JSONObject, host: str) -> str:
    """Report readiness or a recovery route without granting tool permission.

    Args:
        event: Identity-validated session envelope.
        host: Explicit adapter identity.

    Returns:
        Bounded task context with the exact core diagnostic when unavailable.
    """
    # Readiness diagnostics remain advisory to the native host.
    try:
        result = core.execute(request_for(event, "ready", host))
        core.require(result["ok"], result["code"])
    # Convert a failed readiness check into bounded recovery text.
    except core.WorkspaceError as error:
        return recovery(error.code, host)
    return "Task binding checked. Read the assigned packet through the lifecycle read operation."


def recovery(code: str, host: str) -> str:
    """Describe the supported recovery route without copying task content.

    Args:
        code: Bounded diagnostic selected by the adapter or core.
        host: Explicit adapter identity required in lifecycle commands.

    Returns:
        A bounded explanation of the retained state and allowed recovery route.
    """
    return (
        f"TASK_WORKSPACE_NOT_READY: {code}. Use adapters.common.bootstrap_command "
        f"to format the exact project lifecycle command with host={host} and the observed "
        "session ID to diagnose, "
        "register, resume, read and acknowledge the assigned packet. "
        "Unsupported child/provider/background work must use a separately verified route. "
        "Pending work is retained; no completion or readiness was inferred."
    )


def native_observe(event: core.JSONObject, host: str) -> None:
    """Record an advisory ending without settling operations or retiring the session.

    Args:
        event: Identity-validated native envelope.
        host: Explicit adapter identity.
    """
    # Record terminal events as observations only.
    try:
        request = request_for(event, "event", host)
        core.execute({**request, "event_type": "observation", "event": {"code": "UNKNOWN"}})
    # Ignore advisory observation failures without settling work.
    except (core.WorkspaceError, OSError):
        pass  # Advisory host endings are not completion or cleanup evidence.


def run_native(
    handler: Callable[[core.JSONObject], core.JSONObject],
    failure: Callable[[str, str], core.JSONObject],
) -> int:
    """Bound native hook input and runtime while exposing parser errors as exit two.

    Args:
        handler: Native event translator for the selected host.
        failure: Native diagnostic renderer for the selected host.

    Returns:
        Zero for a translated event; two for parser, timeout, or protocol failure.
    """
    return runner.run(handler, failure)
