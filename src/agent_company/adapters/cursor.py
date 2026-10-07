"""Translate local Cursor Agent hooks with native decisions and conversation identity."""

from __future__ import annotations

import os
import sys

from agent_company.adapters import common
from agent_company.lifecycle import task_workspace as core

HOST = "cursor"
PYTHON = common.PYTHON
LIFECYCLE = common.LIFECYCLE


def failure(name: str, code: str) -> core.JSONObject:
    """Render the supported Cursor decision or diagnostic for an event.

    Args:
        name: Native Cursor event name.
        code: Bounded lifecycle or adapter diagnostic.

    Returns:
        Cursor-native JSON without an automatic follow-up or blanket permission grant.
    """
    message = common.recovery(code, HOST)
    if name in {"preToolUse", "subagentStart"}:
        return {"permission": "deny", "user_message": message, "agent_message": message}
    if name == "beforeSubmitPrompt":
        return {"continue": False, "user_message": message}
    if name in {"sessionStart", "postToolUse", "postToolUseFailure"}:
        return {"additional_context": message}
    if name == "preCompact":
        return {"user_message": message}
    return {}


def handle(event: core.JSONObject) -> core.JSONObject:
    """Translate a native Cursor envelope into conversation-bound lifecycle actions.

    Args:
        event: JSON received from a local Cursor Agent hook.

    Returns:
        Native decisions; session start and compaction are advisory only.
    """
    name = event.get("hook_event_name", "")
    try:
        # Local single-root Agent sessions are the only supported Cursor execution surface.
        core.require(os.environ.get("CURSOR_CODE_REMOTE") != "true", "HOST_UNSUPPORTED_REMOTE")
        if name == "subagentStart":
            return failure(name, "HOST_UNSUPPORTED_CHILD_IDENTITY")
        observed = common.native_identity(event, HOST)
        if name == "preToolUse":
            common.native_pre(observed, HOST)
            return {"permission": "allow"}  # Only this validated and recorded tool call passes.
        # Correlate native completion facts with the exact admitted tool call.
        if name in {"postToolUse", "postToolUseFailure"}:
            common.native_post(observed, HOST, failed=name == "postToolUseFailure")
            return {}
        # Cursor prompt decisions use continue; they do not grant tool permission.
        if name == "beforeSubmitPrompt":
            result = common.prompt(observed, HOST)
            if result.get("decision") == "block":
                return {"continue": False, "user_message": result["reason"]}
            return {"continue": True}
        # Startup and compaction provide advisory context only.
        if name == "sessionStart":
            return {"additional_context": common.native_context(observed, HOST)}
        if name == "preCompact":
            return {"user_message": common.native_context(observed, HOST)}
        if name in {"stop", "sessionEnd"}:
            common.native_observe(observed, HOST)
            return {}  # A stopped agent loop is not evidence of finished tools or accepted work.
        return failure(name, "HOST_UNSUPPORTED_EVENT")
    except Exception as error:
        code = error.code if isinstance(error, core.WorkspaceError) else "RECOVERY_REQUIRED"
        return failure(name, code)


def main() -> int:
    """Run one bounded Cursor protocol exchange with fail-closed parser errors.

    Returns:
        Zero for a handled event or two for malformed/unreadable input.
    """
    return common.run_native(handle, failure)


if __name__ == "__main__":
    sys.exit(main())
