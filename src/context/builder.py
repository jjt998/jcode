from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.context.budget import estimate_tokens, tail_clip
from src.context.prefix import render_prefix
from src.context.sections import SECTION_ORDER
from src.context.skills import render_skill_section
from src.runtime.plan import render_runtime_mode_text

if TYPE_CHECKING:
    from src.memory.durable import DurableMemoryStore
    from src.state.workspace import Workspace
    from src.tools.registry import ToolRegistry


@dataclass
class ContextBuildResult:
    context: str
    metadata: dict


class ContextBuilder:
    workspace: Workspace
    durable_memory: DurableMemoryStore
    registry: ToolRegistry
    total_budget: int

    def __init__(self, workspace, durable_memory, registry, total_budget: int = 60000):
        self.workspace = workspace
        self.durable_memory = durable_memory
        self.registry = registry
        self.total_budget = total_budget

    def build(self, session: dict, working_memory, user_message: str) -> ContextBuildResult:
        working_memory.set_retrieval(user_message, self.durable_memory.retrieve(user_message))
        history = self._render_history(session.get("history", []), budget=12000)
        sections = {
            "prefix": render_prefix(self.workspace, self.registry),
            "skill": render_skill_section(),
            "working_memory": self._render_working_memory(session, working_memory),
            "history": history,
            "current_request": "Current user request:\n" + user_message,
        }
        context = "\n\n".join(f"[{name}]\n{sections[name]}" for name in SECTION_ORDER)
        metadata = {
            "sections": {
                name: {"chars": len(sections[name]), "estimated_tokens": estimate_tokens(sections[name])}
                for name in SECTION_ORDER
            },
            "total_chars": len(context),
            "estimated_input_tokens": estimate_tokens(context),
        }
        return ContextBuildResult(context=context, metadata=metadata)

    def _render_working_memory(self, session: dict, working_memory) -> str:
        text = working_memory.render() + f"\n- workspace_root: {self.workspace.root}"
        runtime_mode_text = render_runtime_mode_text(session)
        if runtime_mode_text:
            text += "\n" + runtime_mode_text
        return text

    def _render_history(self, history: list[dict], budget: int) -> str:
        lines = []
        for item in history[-30:]:
            role = item.get("role", "unknown")
            content = str(item.get("content", ""))
            name = item.get("name")
            prefix = f"{role}:{name}" if name else role
            lines.append(f"{prefix}: {content}")
        return tail_clip("\n".join(lines) or "(empty)", budget)
