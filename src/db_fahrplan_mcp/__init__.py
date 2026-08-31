"""db-fahrplan-mcp: Deutsche Bahn timetables as an MCP server (bahn.de, no API key)."""

__version__ = "1.0.0"

from .server import main, mcp  # noqa: E402

__all__ = ["__version__", "main", "mcp"]
