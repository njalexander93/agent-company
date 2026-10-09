"""Validate explicit Desktop connector routing without trusting arbitrary tool names."""

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
