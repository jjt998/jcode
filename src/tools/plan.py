from __future__ import annotations

from src.tools.base import ToolResult


def tool_enter_plan_mode(workspace, args) -> ToolResult:
    return ToolResult("error", "enter_plan_mode requires runtime execution", error_type="runtime_tool")


def tool_exit_plan_mode(workspace, args) -> ToolResult:
    return ToolResult("error", "exit_plan_mode requires runtime execution", error_type="runtime_tool")
