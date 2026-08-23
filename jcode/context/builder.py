from __future__ import annotations

from dataclasses import dataclass

from jcode.context.budget import estimate_tokens, tail_clip
from jcode.context.sections import SECTION_ORDER
from jcode.context.skills import render_skill_section

PREFIX = """You are JCode, a compact local coding agent.

Output protocol:
- To call a tool, return exactly: <tool name="tool_name">{"arg": "value"}</tool>
- To finish, return exactly: <final>answer</final>

Stable safety rules:
- Stay inside the workspace.
- Read files before writing them.
- Do not repeat identical tool calls.
- Shell and write actions may require approval and sandbox checks.
- Summarize evidence from tools before finalizing.
"""


@dataclass
class PromptBuildResult:
    prompt: str
    metadata: dict


class PromptBuilder:
    def __init__(self, workspace, durable_memory, total_budget: int = 60000):
        self.workspace = workspace
        self.durable_memory = durable_memory
        self.total_budget = total_budget

    def build(self, session: dict, working_memory, user_message: str) -> PromptBuildResult:
        working_memory.retrieved_memory = self.durable_memory.retrieve(user_message)
        history = self._render_history(session.get("history", []), budget=12000)
        sections = {
            "prefix": PREFIX.strip(),
            "skill": render_skill_section(),
            "working_memory": working_memory.render() + f"\n- workspace_root: {self.workspace.root}",
            "history": history,
            "current_request": "Current user request:\n" + user_message,
        }
        prompt = "\n\n".join(f"[{name}]\n{sections[name]}" for name in SECTION_ORDER)
        metadata = {
            "sections": {
                name: {"chars": len(sections[name]), "estimated_tokens": estimate_tokens(sections[name])}
                for name in SECTION_ORDER
            },
            "total_chars": len(prompt),
            "estimated_input_tokens": estimate_tokens(prompt),
        }
        return PromptBuildResult(prompt=prompt, metadata=metadata)

    def _render_history(self, history: list[dict], budget: int) -> str:
        lines = []
        for item in history[-30:]:
            role = item.get("role", "unknown")
            content = str(item.get("content", ""))
            name = item.get("name")
            prefix = f"{role}:{name}" if name else role
            lines.append(f"{prefix}: {content}")
        return tail_clip("\n".join(lines) or "(empty)", budget)
