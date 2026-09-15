from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolSetProfile:
    name: str
    allowed_tools: frozenset[str]

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools


def build_tool_profiles(registry: ToolRegistry) -> dict[str, ToolSetProfile]:
    tools = registry.tools
    all_tools = frozenset(tools)
    workspace_read_only = frozenset({"read_file", "list_files", "search"}) & all_tools
    shared_runtime = frozenset({"todo_add", "todo_update", "todo_list", "todo_delete", "todo_archive", "ask_user", "enter_plan_mode", "exit_plan_mode"})
    subagent_tools = frozenset({"spawn_subagent", "send_subagent_message", "wait_subagent"})
    plan_tools = workspace_read_only | shared_runtime | subagent_tools | frozenset({"write_file", "apply_patch"})
    worker_tools = all_tools - frozenset({"run_shell", "ask_user", "enter_plan_mode", "exit_plan_mode"}) - subagent_tools - shared_runtime
    tester_tools = workspace_read_only | frozenset({"run_shell"})
    dream_tools = workspace_read_only | frozenset({"write_file", "apply_patch"})
    return {
        "default": ToolSetProfile("default", all_tools),
        "plan": ToolSetProfile("plan", plan_tools & all_tools),
        "readonly": ToolSetProfile("readonly", workspace_read_only),
        "worker": ToolSetProfile("worker", worker_tools & all_tools),
        "tester": ToolSetProfile("tester", tester_tools & all_tools),
        "dream": ToolSetProfile("dream", dream_tools & all_tools),
    }
