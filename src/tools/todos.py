from __future__ import annotations

from src.tools.base import ToolResult


def tool_todo_add(workspace, args) -> ToolResult:
    return ToolResult("error", "todo_add requires runtime execution", error_type="runtime_tool")


def tool_todo_update(workspace, args) -> ToolResult:
    return ToolResult("error", "todo_update requires runtime execution", error_type="runtime_tool")


def tool_todo_list(workspace, args) -> ToolResult:
    return ToolResult("error", "todo_list requires runtime execution", error_type="runtime_tool")
