"""Import shim across the MCP Python SDK 1.x / 2.x rename (FastMCP -> MCPServer)."""

from __future__ import annotations

try:  # mcp >= 2.0
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver.exceptions import ToolError
except ModuleNotFoundError:  # mcp 1.10 .. 1.x
    from mcp.server.fastmcp import Context, FastMCP  # type: ignore[attr-defined,no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

from mcp.types import ToolAnnotations


def read_only_annotations() -> ToolAnnotations:
    """ToolAnnotations fields are camelCase in mcp 1.x and snake_case in 2.x (aliases accept
    both at runtime, but type checkers see only one). Build via the wire names."""
    return ToolAnnotations.model_validate({"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True})


__all__ = ["Context", "FastMCP", "ToolAnnotations", "ToolError", "read_only_annotations"]
