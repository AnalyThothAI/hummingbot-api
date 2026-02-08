"""Tool handler dispatch for MCP adapter."""

from mcp.tool_registry import dispatch_tool, UnknownToolError

__all__ = ["dispatch_tool", "UnknownToolError"]
