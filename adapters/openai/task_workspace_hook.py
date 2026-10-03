#!/usr/bin/env python3
"""Bounded Codex wire adapter. Host trust/coverage is a separate acceptance gate."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from operations.memory import task_workspace as core

BOOTSTRAP_FIELDS = {
    "diagnose": set(), "register": {"main_worktree", "startup"},
    "bind": set(), "adopt": {"coordinator", "inventory", "owners", "evidence"},
    "resume": set(), "restore": {"observations"},
    "read": set(), "acknowledge": {"packet_digest"},
    "archive-index": {"observations"}, "archive-verify": {"observations"},
    "archive-observe-save": {"document_id", "content_digest"},
    "cleanup-plan": set(), "cleanup-commit": {"observations", "cleanup_challenge"},
    "reopen": {"evidence"},
    "archive-prepare": {"seal"},
    "reconcile-files": {"inventory", "evidence"},
    "rebind": {"new_issue_id", "new_binding_generation", "evidence"},
}
COMMON_FIELDS = {"schema_version", "operation", "request_id", "worktree", "host", "session_id",
                 "repo_id", "issue_id", "issue_uuid", "binding_generation", "expected_revision"}
LIFECYCLE = ROOT / "operations/memory/task_workspace.py"
PYTHON = "/usr/bin/python3"


def denial(code):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "TASK_WORKSPACE_NOT_READY: " + code +
            ". Use the exact lifecycle --request-json command for the assigned session."}}


def request_for(event, operation):
    request = {"schema_version": 1, "operation": operation, "request_id": str(uuid.uuid4()),
               "worktree": event["cwd"], "host": "codex", "session_id": event["session_id"]}
    diagnosis = core.execute({**request, "operation": "diagnose"})
    if not diagnosis["ok"] or diagnosis["code"] != "REGISTERED":
        raise core.WorkspaceError(diagnosis["code"])
    request["repo_id"] = diagnosis["repo_id"]
    with core.Store(request) as store:
        binding = store.binding()
        if not binding:
            raise core.WorkspaceError("BINDING_MISSING")
        request.update(binding)
    return request


def bootstrap(event, ready=False):
    """Require the exact shell spelling produced by shlex.join: no wrappers/redirection.

    JSON is a single shell-quoted argument. Matching the canonical serialization
    prevents expansion even inside quote tricks. A missing local assignment permits
    only registration/diagnosis; the prompt assignment pins the issue and session.
    """
    if event.get("tool_name") not in {"Bash", "exec_command"}:
        return False
    args = event.get("tool_input", {})
    if (not isinstance(args, dict) or args.get("tty") or args.get("login") is not False
            or args.get("shell") != "/bin/sh" or not set(args) <= {
                "command", "cmd", "login", "shell", "workdir", "yield_time_ms", "max_output_tokens",
                "sandbox_permissions", "justification", "prefix_rule"}):
        return False
    command = args.get("command", args.get("cmd"))
    if not isinstance(command, str) or len(command.encode()) > 65536:
        return False
    try:
        argv = shlex.split(command)
        if len(argv) != 4 or argv[:3] != [PYTHON, str(LIFECYCLE), "--request-json"]:
            return False
        if command != shlex.join(argv):
            return False
        request = core.strict_json(argv[3])
        if not isinstance(request, dict):
            return False
        operation = request.get("operation")
        if not ready and (operation not in BOOTSTRAP_FIELDS or
                          not set(request) <= COMMON_FIELDS | BOOTSTRAP_FIELDS[operation]):
            return False
        if request.get("session_id") != event["session_id"] or request.get("host", "codex") != "codex":
            return False
        if core.repository(request["worktree"])[0] != core.repository(event["cwd"])[0]:
            return False
        if request["operation"] in {"register", "diagnose"}:
            return set(request) <= {"schema_version", "operation", "request_id", "worktree", "host",
                                    "session_id", "main_worktree", "startup"}
        with core.Directory.absolute(core.repository(event["cwd"])[0]) as root, root.child(".task") as local:
            with local.child(".bindings") as bindings:
                assignment = bindings.json(core.participant_key(request) + ".assignment.json")
        return request.get("issue_id") == assignment["issue_id"]
    except (core.WorkspaceError, OSError, ValueError, KeyError, TypeError):
        return False



def provider_gate(event):
    tool = event.get("tool_name", "")
    if tool not in {"mcp__codex_apps__linear_save_document", "mcp__codex_apps__linear_get_document"}:
        return False
    request = request_for(event, "ready")
    with core.Store(request) as store, store.issues.child(request["issue_id"]) as control, control.lock():
        issue = core.Issue(store, control, request["issue_id"])
        issue.recover()
        state = issue.state
        core.authorize(state, request, coordinator=True, maintenance=True)
        if state["storage"] != "cleaned":
            issue.files()
        args = event.get("tool_input", {})
        if tool.endswith("linear_get_document"):
            identifiers = {x["id"] for x in state.get("provider_saves", {}).values() if x.get("id")}
            if state.get("archive"):
                identifiers.add(state["archive"]["root"]["id"])
                identifiers.update(x["id"] for x in state["archive"]["parts"])
            return set(args) == {"id"} and args["id"] in identifiers
        if state["storage"] == "cleaned":
            return False
        export = control.json("export-" + state["export"]["snapshot"] + ".json")
        documents = export["parts"] + ([state["index_request"]] if state.get("index_request") else [])
        if args not in documents:
            return False
        digest = core.sha(args["content"].encode())
    result = core.execute({**request, "operation": "archive-save-start", "content_digest": digest})
    return result["ok"]



def automatic_attach(event, identifier):
    base = {"schema_version": 1, "request_id": "startup:" + event["session_id"],
            "operation": "diagnose", "worktree": event["cwd"], "host": "codex", "session_id": event["session_id"]}
    diagnosis = core.execute(base)
    if diagnosis.get("code") != "REGISTERED":
        return
    base.update(repo_id=diagnosis["repo_id"], issue_id=identifier)
    with core.Store(base) as store:
        binding = store.binding()
        if binding:
            core.require(binding["issue_id"] == identifier, "BINDING_CONFLICT")
        key = core.participant_key(base)
        filename = key + ".startup.json"
        setup = store.bindings.json(filename) if store.bindings.exists(filename) else None
        if binding:
            with store.issues.child(identifier) as control, control.lock():
                issue = core.Issue(store, control, identifier)
                issue.recover()
                member = issue.state["participants"].get(key, {})
                if member.get("packet") is not None:
                    return
    if setup:
        core.require(setup["issue_id"] == identifier, "BINDING_CONFLICT")
        result = core.execute({**base, "operation": "create", "coordinator": setup["coordinator"],
                               "issue_uuid": setup["issue_uuid"]})
        core.require(result["ok"], result["code"])
        # A previous scope failure may follow successful creation. Read current revision
        # and retry only the still-missing assignment, never replace a later packet.
        diagnostic = core.execute({**base, "operation": "diagnose", "request_id": base["request_id"] + ":diagnose"})
        result = core.execute({**base, "operation": "scope", "request_id": base["request_id"] + ":scope",
                               "binding_generation": result["binding_generation"],
                               "expected_revision": diagnostic["revision"], "target_participant": key,
                               "packet": setup["packet"]})
        core.require(result["ok"], result["code"])
    elif not binding:
        # Existing coordinator assignment in shared state is required for a new join.
        result = core.execute({**base, "operation": "attach"})
        if not result["ok"] and result["code"] not in {"BINDING_MISSING", "SCOPE_MISSING"}:
            raise core.WorkspaceError(result["code"])


def prompt(event):
    lines = [line for line in event.get("prompt", "").splitlines() if line.startswith("Task:")]
    if not lines:
        return {}
    if len(lines) != 1 or not re.fullmatch(r"Task: [A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,9}", lines[0]):
        return {"decision": "block", "reason": "BINDING_CONFLICT: Supply exactly one Task: ISSUE-ID line."}
    identifier = lines[0][6:]
    request = {"host": "codex", "session_id": event["session_id"]}
    root, _, _ = core.repository(event["cwd"])
    with core.Directory.absolute(root) as worktree, worktree.child(".task", True) as local, \
            local.child(".bindings", True) as bindings, bindings.lock("assignment.lock"):
        key = core.participant_key(request)
        for name in (key + ".json", key + ".assignment.json"):
            if bindings.exists(name) and bindings.json(name)["issue_id"] != identifier:
                return {"decision": "block", "reason": "BINDING_CONFLICT: Explicit rebind is required."}
        bindings.put(key + ".assignment.json", {"issue_id": identifier})
    automatic_attach(event, identifier)
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
            "Task identity recorded. Explicitly assigned workspace setup was attempted. Read and acknowledge the permitted packet before task tools; use the lifecycle diagnostic route if setup is missing."}}


def handle(event):
    name = event.get("hook_event_name")
    if name == "UserPromptSubmit":
        return prompt(event)
    if name == "PermissionRequest":
        return {}  # Never grant host permissions.
    if name == "PreToolUse":
        if bootstrap(event):
            return {}
        try:
            if provider_gate(event):
                return {}
        except core.WorkspaceError:
            pass
        tool = event.get("tool_name", "")
        if any(s in tool for s in ("spawn_agent", "create_thread", "fork_thread", "send_message_to_thread", "followup_task")):
            return denial("HOST_UNSUPPORTED_CHILD_IDENTITY")
        if tool in {"request_user_input", "request_user_input_async"}:
            return {}
        request = request_for(event, "ready")
        result = core.execute(request)
        if not result["ok"]:
            return denial(result["code"])
        if bootstrap(event, ready=True):
            return {}  # Local lifecycle owns its transaction; don't count itself as pending external work.
        result = core.execute({**request, "operation": "tool-start", "request_id": "pre:" + event["tool_use_id"],
                               "tool_id": event["tool_use_id"]})
        return {} if result["ok"] else denial(result["code"])
    if name == "PostToolUse":
        request = request_for(event, "tool-complete")
        response = event.get("tool_response", {})
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except ValueError:
                response = {}
        if not isinstance(response, dict):
            response = {}
        # Unknown response shapes keep the operation pending; never infer completion.
        handle_id = response.get("session_id")
        completed = response.get("exit_code") is not None or response.get("isError") is not None
        if not completed and not handle_id:
            return {}
        core.execute({**request, "request_id": "post:" + event["tool_use_id"], "tool_id": event["tool_use_id"],
                      "completed": completed, "async_handle": str(handle_id) if handle_id else None})
        return {}
    if name == "SubagentStart":
        return {"systemMessage": "HOST_UNSUPPORTED: Native child-to-tool identity is not verified. No child readiness is established."}
    if name in {"SessionStart", "PreCompact", "PostCompact"}:
        try:
            result = core.execute(request_for(event, "ready"))
        except core.WorkspaceError:
            result = {"ok": False, "code": "BINDING_MISSING"}
        if result["ok"] and name in {"PreCompact", "PostCompact"}:
            return {}
        if result["ok"]:
            return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                    "Task binding checked. Retrieve the permitted packet through the lifecycle read operation."}}
        if name == "SessionStart" and result["code"] == "BINDING_MISSING":
            return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                    "Task workspace is unbound. Supply one Task: ISSUE-ID line and use the lifecycle bootstrap route."}}
        return {"continue": False, "stopReason": "TASK_WORKSPACE_NOT_READY: " + result["code"]}
    if name in {"Stop", "Interrupt", "SessionEnd", "SubagentStop"}:
        try:
            request = request_for(event, "event")
            core.execute({**request, "event_type": "observation", "event": {
                "code": "INTERRUPTED" if name == "Interrupt" else "UNKNOWN"}})
        except (core.WorkspaceError, OSError):
            pass  # Optional observations cannot establish completion or retirement.
    return {}


def main():
    name = None
    def timeout(*_):
        raise core.WorkspaceError("BUSY")
    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        core.require(len(raw) <= 1024 * 1024, "SIZE_LIMIT")
        event = core.strict_json(raw)
        name = event.get("hook_event_name")
        result = handle(event)
    except Exception as error:
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        result = denial(code) if name == "PreToolUse" else (
            {"decision": "block", "reason": "TASK_WORKSPACE_NOT_READY: " + code} if name == "UserPromptSubmit" else
            {"systemMessage": "TASK_WORKSPACE_NOT_READY: " + code})
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
