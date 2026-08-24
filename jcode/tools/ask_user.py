from __future__ import annotations

from jcode.tools.base import ToolResult


def tool_ask_user(workspace, args) -> ToolResult:
    return ToolResult("error", "ask_user requires runtime execution", error_type="runtime_tool")
