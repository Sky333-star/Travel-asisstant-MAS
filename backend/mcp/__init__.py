"""MCP integration: registry, supervised client manager, agent-facing tools."""

from . import tools
from .client import MCPConnection, MCPError, MCPManager, ServerState, mcp_manager
from .registry import SERVERS, MCPServerSpec, enabled_specs, tool_owner

__all__ = [
    "SERVERS",
    "MCPConnection",
    "MCPError",
    "MCPManager",
    "MCPServerSpec",
    "ServerState",
    "enabled_specs",
    "mcp_manager",
    "tool_owner",
    "tools",
]
