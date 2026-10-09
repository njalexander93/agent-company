"""Exercise configured Desktop Linear reads through the real startup lifecycle."""

import json

import pytest

from agent_company.adapters import claude, common
from agent_company.lifecycle import task_workspace as core
from tests.integration.adapters.test_native_hosts import native_event, ticket_callbacks
from tests.support import Fixture

pytestmark = pytest.mark.integration
CONNECTOR = "01234567-89ab-4cde-8f01-23456789abcd"


@pytest.mark.parametrize("source", ["claudeai", "dynamic", "sdk"])
@pytest.mark.parametrize("content_blocks", [False, True])
def test_desktop_connector_requires_mapping_and_exact_correlated_read(
    monkeypatch: pytest.MonkeyPatch, source: str, content_blocks: bool
) -> None:
    """Admit only the configured provider and settle its exact issue read.

    Args:
        monkeypatch: Scoped environment configuration for the connector binding.
        source: Documented remote connector provenance.
        content_blocks: Use Desktop's observed native result instead of the normalized fixture.
    """
    # Create a disposable repository; never register the user's active session.
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    monkeypatch.delenv("AGENT_COMPANY_CLAUDE_LINEAR_CONNECTOR_ID", raising=False)
    case = Fixture()
    case.setUp()
    case.base["host"] = claude.HOST
    case.base["coordinator"] = core.participant_key(case.base)
    try:
        # Record the selected ticket and construct Desktop's UUID-qualified callbacks.
        claude.handle(native_event("claude", case, "UserPromptSubmit", prompt="Task: TEST-1"))
        issue = {"id": "c0a8f3be-1c13-4f2b-9a1e-b2e61f11f977", "identifier": "TEST-1"}
        before, after = ticket_callbacks("claude", case, issue)
        # Desktop supplies the successful MCP content list without a CallToolResult wrapper.
        if content_blocks:
            after["tool_response"] = [
                {"type": "text", "text": json.dumps({"id": "TEST-1", "uuid": issue["id"]})}
            ]
        for event in [*before, after]:
            event.update(
                tool_name=f"mcp__{CONNECTOR}__get_issue",
                mcp_server={"name": CONNECTOR, "source": source},
            )
        # A UUID alone grants no exception, even when the host reports a known source.
        assert "TICKET_READ_REQUIRED" in str(claude.handle(before[0]))
        monkeypatch.setenv("AGENT_COMPANY_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
        # Wrong issues remain denied and must not create a workspace.
        wrong = {**before[0], "tool_input": {"id": "TEST-2"}}
        assert "BINDING_CONFLICT" in str(claude.handle(wrong))
        assert not (case.root / ".task/TEST-1").exists()
        # Admit the real read, reject a mismatched completion, then settle the right call.
        assert claude.handle(before[0]) == {}
        assert "BINDING_CONFLICT" in str(claude.handle({**after, "tool_use_id": "different-call"}))
        assert not (case.root / ".task/TEST-1").exists()
        # Multiple content blocks remain invalid and cannot create workspace state.
        if content_blocks:
            assert "PROVIDER_RESPONSE_INVALID" in str(
                claude.handle({**after, "tool_response": [{"type": "text", "text": "{}"}] * 2})
            )
            assert not (case.root / ".task/TEST-1").exists()
            assert claude.handle(before[0]) == {}
        assert "TASK_WORKSPACE_READY" in str(claude.handle(after))
        observed = common.native_identity(native_event("claude", case, "SessionStart"), claude.HOST)
        assert core.execute(common.request_for(observed, "ready", claude.HOST))["ok"] is True
    finally:
        # Release filesystem state even when a denial or readiness assertion fails.
        case.doCleanups()
