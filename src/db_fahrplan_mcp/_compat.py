"""Import shim across the MCP Python SDK 1.x / 2.x rename (FastMCP -> MCPServer)."""

from __future__ import annotations

try:  # mcp >= 2.0
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver.exceptions import ToolError
except ModuleNotFoundError:  # mcp 1.2 .. 1.x
    from mcp.server.fastmcp import Context, FastMCP
    from mcp.server.fastmcp.exceptions import ToolError

from mcp.types import ToolAnnotations

__all__ = ["Context", "FastMCP", "ToolAnnotations", "ToolError"]
