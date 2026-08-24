from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jcode.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolSetProfile:
    name: str
    allowed_tools: frozenset[str]

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools


def build_tool_profiles(registry: ToolRegistry) -> dict[str, ToolSetProfile]:
    tools = registry.tools
    all_tools = frozenset(tools)
    read_only = frozenset(name for name, tool in tools.items() if tool.read_only)
    shared_runtime = frozenset({"todo_add", "todo_update", "todo_list", "ask_user", "enter_plan_mode", "exit_plan_mode"})
    plan_tools = read_only | shared_runtime | frozenset({"write_file", "apply_patch"})
    worker_tools = all_tools - frozenset({"run_shell", "ask_user", "enter_plan_mode", "exit_plan_mode"})
    dream_tools = read_only | frozenset({"write_file", "apply_patch"})
    return {
        "default": ToolSetProfile("default", all_tools),
        "plan": ToolSetProfile("plan", plan_tools & all_tools),
        "readonly": ToolSetProfile("readonly", read_only),
        "worker": ToolSetProfile("worker", worker_tools & all_tools),
        "dream": ToolSetProfile("dream", dream_tools & all_tools),
    }
