"""Compatibility shim across MCP Python SDK generations.

The MCP SDK renamed its high-level server class in 2.0:

* SDK 1.x -- ``from mcp.server.fastmcp import FastMCP``
* SDK 2.x -- ``from mcp.server.mcpserver import MCPServer``

Both expose the same surface this project uses (``@server.tool()`` to register
a tool, ``server.run(transport=...)`` to serve), so one shim keeps all five
servers working on whichever generation is installed, rather than pinning the
project to a version that will age out.

The *client* side of the SDK -- ``ClientSession``, ``StdioServerParameters``,
``stdio_client`` -- is unchanged across both versions, so the backend needs no
equivalent shim.
"""

from __future__ import annotations

from typing import Any

_SDK_GENERATION: str = "unknown"


def _load_server_class() -> tuple[Any, str]:
    """Return ``(ServerClass, generation)`` for the installed SDK."""
    try:  # SDK 2.x
        from mcp.server.mcpserver import MCPServer  # type: ignore[import-not-found]

        return MCPServer, "2.x"
    except ImportError:
        pass

    try:  # SDK 1.x
        from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]

        return FastMCP, "1.x"
    except ImportError as exc:  # pragma: no cover - environment error
        raise ImportError(
            "No supported MCP server class found. Install the MCP SDK with "
            "`pip install 'mcp>=1.9'`."
        ) from exc


def create_server(name: str, **kwargs: Any) -> Any:
    """Create an MCP server instance for whichever SDK generation is present.

    Args:
        name: Server name advertised to clients during initialisation.
        **kwargs: Passed through to the underlying server class.
    """
    global _SDK_GENERATION
    server_class, generation = _load_server_class()
    _SDK_GENERATION = generation
    return server_class(name, **kwargs)


def sdk_generation() -> str:
    """Which SDK generation was detected ("1.x", "2.x" or "unknown")."""
    if _SDK_GENERATION == "unknown":
        try:
            _load_server_class()
        except ImportError:
            return "unknown"
    return _SDK_GENERATION
