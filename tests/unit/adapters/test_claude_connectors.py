"""Validate explicit Desktop connector routing without trusting arbitrary tool names."""

from pathlib import Path

import pytest

from agent_company.adapters import common
from agent_company.lifecycle import task_workspace as core
from tests.types import JsonObject

pytestmark = pytest.mark.unit
CONNECTOR = "01234567-89ab-4cde-8f01-23456789abcd"


@pytest.mark.parametrize("source", ["claudeai", "dynamic", "sdk"])
def test_connector_binding_requires_matching_host_provenance(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """Keep routing, source validation and argument checks on configured connectors.

    Args:
        monkeypatch: Scoped configuration for the verified Linear connector.
        source: Accepted documented remote-server source.
    """
    # Configure a single verified connector and construct its exact read operation.
    monkeypatch.setenv("AGENT_COMPANY_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    event: JsonObject = {
        "tool_name": f"mcp__{CONNECTOR}__get_issue",
        "mcp_server": {"name": CONNECTOR, "source": source},
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "native-call",
    }
    assert common._linear_tool(event, "claude-code") == ("native-call", {"id": "TEST-1"})
    # Missing, mismatched, local or unknown provenance cannot borrow the connector mapping.
    for server in [
        None,
        {"name": "linear", "source": source},
        {"name": [], "source": source},
        *[{"name": CONNECTOR, "source": value} for value in ["user", "unknown", None]],
    ]:
        with pytest.raises(core.WorkspaceError) as failure:
            common._linear_tool({**event, "mcp_server": server}, "claude-code")
        assert failure.value.code == "HOST_UNSUPPORTED_PROVIDER"
    # Other operations and other connector IDs never qualify for the ticket-read exception.
    for name in [f"mcp__{CONNECTOR}__save_issue", "mcp__other__get_issue", "ToolSearch"]:
        assert common._linear_tool({**event, "tool_name": name}, "claude-code") is None
    with pytest.raises(core.WorkspaceError) as failure:
        common._linear_tool({**event, "tool_input": []}, "claude-code")
    assert failure.value.code == "INVALID_REQUEST"
    # Invalid configuration must neither admit the opaque connector nor affect named Linear.
    for configured in ["", "*", "linear", CONNECTOR + ",other", CONNECTOR.upper()]:
        monkeypatch.setenv("AGENT_COMPANY_CLAUDE_LINEAR_CONNECTOR_ID", configured)
        assert common._linear_tool(event, "claude-code") is None
    named = {
        **event,
        "tool_name": "mcp__linear__get_issue",
        "mcp_server": {"name": "linear", "source": "user"},
    }
    assert common._linear_tool(named, "claude-code") == ("native-call", {"id": "TEST-1"})


@pytest.mark.parametrize("operation", sorted(common.LINEAR_PROVIDER_OPERATIONS))
def test_provider_admission_accepts_only_allowlisted_configured_operations(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Admit each allowlisted operation only on the configured server and provenance.

    Args:
        monkeypatch: Scoped configuration for the verified Linear connector.
        operation: One allowlisted Linear operation.
    """
    # Configure one connector and build its operation event.
    monkeypatch.setenv("AGENT_COMPANY_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    event: JsonObject = {
        "tool_name": f"mcp__{CONNECTOR}__{operation}",
        "mcp_server": {"name": CONNECTOR, "source": "sdk"},
        "tool_input": {},
        "tool_use_id": "native-call",
    }
    assert common._linear_provider_tool(event, "claude-code") == operation
    named = {
        **event,
        "tool_name": f"mcp__linear__{operation}",
        "mcp_server": {"name": "linear", "source": "project"},
    }
    assert common._linear_provider_tool(named, "claude-code") == operation
    # Another host, provenance, server or mismatched name never qualifies.
    for changed, host in [
        (event, "cursor"),
        ({**event, "mcp_server": {"name": CONNECTOR, "source": "user"}}, "claude-code"),
        ({**event, "mcp_server": None}, "claude-code"),
        ({**event, "tool_name": f"mcp__other__{operation}"}, "claude-code"),
        ({**event, "mcp_server": {"name": "other", "source": "user"}}, "claude-code"),
        ({**named, "tool_name": f"mcp__linear-server__{operation}"}, "claude-code"),
    ]:
        assert common._linear_provider_tool(changed, host) is None


@pytest.mark.parametrize(
    "operation", ["save_document", "get_document", "delete_comment", "list_issues", "get_issue_x"]
)
def test_provider_admission_rejects_other_operations(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Keep document, archive and unlisted operations outside the native route.

    Args:
        monkeypatch: Scoped configuration for the verified Linear connector.
        operation: An operation outside the allowlist.
    """
    # Even the configured connector cannot run an unlisted operation.
    monkeypatch.setenv("AGENT_COMPANY_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    event: JsonObject = {
        "tool_name": f"mcp__{CONNECTOR}__{operation}",
        "mcp_server": {"name": CONNECTOR, "source": "sdk"},
        "tool_input": {},
        "tool_use_id": "native-call",
    }
    assert common._linear_provider_tool(event, "claude-code") is None
    with pytest.raises(core.WorkspaceError) as failure:
        common.native_tool(event, "claude-code")
    assert failure.value.code == "HOST_UNSUPPORTED_PROVIDER"


def test_native_tool_routes_providers_and_no_effect_tools_by_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admit configured Claude provider calls and no-effect tools; keep Cursor unchanged.

    Args:
        monkeypatch: Scoped configuration for the verified Linear connector.
    """
    # A configured Claude provider write is an ordinary correlated tool.
    monkeypatch.setenv("AGENT_COMPANY_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    save: JsonObject = {
        "tool_name": f"mcp__{CONNECTOR}__save_issue",
        "mcp_server": {"name": CONNECTOR, "source": "claudeai"},
        "tool_input": {"id": "TEST-1"},
        "tool_use_id": "native-call",
    }
    assert common.native_tool(save, "claude-code") == save["tool_name"]
    # Cursor's generic MCP callback remains unsupported after readiness.
    for event, host, code in [
        (
            {"tool_name": "MCP:get_issue", "tool_input": {}, "tool_use_id": "c"},
            "cursor",
            "HOST_UNSUPPORTED_PROVIDER",
        ),
        (
            {**save, "tool_name": "ToolSearch", "mcp_server": None},
            "cursor",
            "HOST_UNSUPPORTED_TOOL",
        ),
        (
            {"tool_name": "Skill", "tool_input": {}, "tool_use_id": "c"},
            "cursor",
            "HOST_UNSUPPORTED_TOOL",
        ),
    ]:
        with pytest.raises(core.WorkspaceError) as failure:
            common.native_tool(event, host)
        assert failure.value.code == code
    # Claude admits schema and skill loads by name.
    for tool in ["ToolSearch", "Skill"]:
        event = {"tool_name": tool, "tool_input": {}, "tool_use_id": "c"}
        assert common.native_tool(event, "claude-code") == tool


@pytest.mark.parametrize("tool", ["ToolSearch", "Skill"])
def test_no_effect_tools_require_readiness_but_record_no_pending_work(
    monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    """Check readiness for schema and skill loads without tool-start or settlement.

    Args:
        monkeypatch: Replaces lifecycle boundaries and records core calls.
        tool: No-effect Claude tool.
    """
    # Record core calls while readiness succeeds.
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        common, "request_for", lambda _event, operation, _host: {"operation": operation}
    )
    calls: list[JsonObject] = []
    monkeypatch.setattr(
        common.core, "execute", lambda request: calls.append(request) or {"ok": True, "code": "OK"}
    )
    event: JsonObject = {"tool_name": tool, "tool_input": {}, "tool_use_id": "tool-1"}
    # The pre-hook checks readiness only and the post-hook settles nothing.
    common.native_pre(event, "claude-code")
    assert [call["operation"] for call in calls] == ["ready"]
    common.native_post({**event, "tool_response": "ok"}, "claude-code", failed=False)
    assert [call["operation"] for call in calls] == ["ready"]
    # A failed readiness check still denies the load.
    monkeypatch.setattr(
        common.core, "execute", lambda _request: {"ok": False, "code": "SOURCE_STALE"}
    )
    with pytest.raises(core.WorkspaceError) as failure:
        common.native_pre(event, "claude-code")
    assert failure.value.code == "SOURCE_STALE"


def test_preparation_tool_bounds_tool_search_and_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Accept only bounded issue-read schema loads and exact governing-file reads.

    Args:
        monkeypatch: Scoped connector configuration and repository resolution.
        tmp_path: Disposable checkout root.
    """
    # Resolve every event to the disposable checkout.
    monkeypatch.setenv("AGENT_COMPANY_CLAUDE_LINEAR_CONNECTOR_ID", CONNECTOR)
    monkeypatch.setattr(common.core, "repository", lambda _path: (tmp_path, tmp_path, [tmp_path]))
    agents = str(tmp_path / "AGENTS.md")

    def event(tool: str, args: object) -> JsonObject:
        """Build one Claude preparation event.

        Args:
            tool: Native tool name.
            args: Native tool input.

        Returns:
            The native envelope.
        """
        return {"cwd": str(tmp_path), "tool_name": tool, "tool_input": args, "tool_use_id": "t"}

    # Positive selections and reads.
    selection = f"select:mcp__{CONNECTOR}__get_issue, mcp__linear__get_issue"
    for tool, args in [
        ("ToolSearch", {"query": selection}),
        ("ToolSearch", {"query": "mcp__linear-server__get_issue", "max_results": 20}),
        ("Read", {"file_path": agents, "limit": 10}),
        ("Bash", {"command": f"cat '{agents}'", "timeout": 1000}),
    ]:
        assert common.preparation_tool(event(tool, args), "claude-code") is True, args
        assert common.preparation_tool(event(tool, args), "cursor") is False
    # Malformed inputs, widened searches and other files are rejected.
    for tool, args in [
        ("ToolSearch", "select:get_issue"),
        ("ToolSearch", {"query": "select:"}),
        ("ToolSearch", {"query": "get_issue", "max_results": 0}),
        ("ToolSearch", {"query": "get_issue", "max_results": 21}),
        ("ToolSearch", {"query": "get_issue", "max_results": True}),
        ("ToolSearch", {"query": "get_issue", "extra": 1}),
        ("ToolSearch", {"query": "get_issue " + "x" * 200}),
        ("ToolSearch", {"query": "get_issue; select:Bash"}),
        ("ToolSearch", {"query": "select:mcp__linear__list_issues"}),
        ("Read", {"file_path": agents, "pages": "1"}),
        ("Read", {"file_path": str(tmp_path / "docs/runtime/development.md")}),
        ("Bash", {"command": "cat AGENTS.md"}),
        ("Bash", {"command": f"cat {agents}\nrm -rf ."}),
        ("Bash", {"command": f"cat '{agents}"}),
        ("Bash", {"command": f"cat {agents}", "extra": True}),
        ("Write", {"file_path": agents}),
    ]:
        assert common.preparation_tool(event(tool, args), "claude-code") is False, args
