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
    worker_tools = all_tools - frozenset({"run_shell"})
    dream_tools = read_only | frozenset({"write_file", "apply_patch"})
    return {
        "default": ToolSetProfile("default", all_tools),
        "readonly": ToolSetProfile("readonly", read_only),
        "worker": ToolSetProfile("worker", worker_tools & all_tools),
        "dream": ToolSetProfile("dream", dream_tools & all_tools),
    }
