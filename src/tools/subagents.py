from __future__ import annotations

from src.tools.base import ToolResult

SUBAGENT_TOOL_NAMES = {"spawn_subagent", "send_subagent_message", "wait_subagent"}


def tool_spawn_subagent(workspace, args) -> ToolResult:
    return ToolResult("error", "spawn_subagent requires runtime execution", error_type="runtime_tool")


def tool_send_subagent_message(workspace, args) -> ToolResult:
    return ToolResult("error", "send_subagent_message requires runtime execution", error_type="runtime_tool")


def tool_wait_subagent(workspace, args) -> ToolResult:
    return ToolResult("error", "wait_subagent requires runtime execution", error_type="runtime_tool")
