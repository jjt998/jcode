from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.state.workspace import Workspace
    from src.tools.registry import ToolRegistry

PROJECT_RULES_FILE = "JCODE.md"
PROJECT_RULES_MAX_CHARS = 12000


SYSTEM_RULES = """System rules:
- You are JCode, a compact local coding agent.
"""


OUTPUT_PROTOCOL = """Output protocol:
Return exactly one primary protocol block per response: exactly one of <tool>, <tools>, or <final>.
Optional reasoning may appear before the primary protocol block as <reasoning>internal planning only</reasoning>.
Reasoning is for internal task planning and must be written in Chinese. Do not put greetings, final-answer prefixes, tone, or final formatting in reasoning.
Do not acknowledge final-response style rules in reasoning.
For one tool, return exactly: <tool name="tool_name">{"arg":"value"}</tool>
For multiple tools, return exactly: <tools>[{"name":"tool_name","args":{"arg":"value"}}]</tools>.
The <tools> content must be a JSON array. Each item must contain "name" and an object-valued "args" field. Tools execute in array order.
<tool> must close with </tool>; <tools> must close with </tools>; <final> must close with </final>. Never use <tools>...</tool> or <tool>...</tools>.
For the final answer, return exactly: <final>answer</final>. Project final-response style rules apply only inside <final>.
Apply greetings, tone, prefixes, and answer formatting only inside <final>. Do not apply them to reasoning, tool, or tools blocks.
Do not output natural language outside the allowed protocol blocks. Do not output more than one primary protocol block. Do not add Markdown fences around protocol blocks.
"""


STABLE_SAFETY_RULES = """Stable safety rules:
- Stay inside the workspace.
- Read files before writing them.
- Do not repeat identical tool calls.
- Shell and write actions may require approval and sandbox checks.
- Summarize evidence from tools before finalizing.
- If a tool result starts with a workspace-relative artifact path, treat that path as the full result artifact and read it when you need the complete output.
- When reading a large artifact under .jcode/runs/.../artifacts/, use read_file with start and end to inspect it in segments. Artifact reads are returned directly and must not be externalized again.
- A read_file result marked stale describes an older file version. Treat it as historical evidence only and read the current file before relying on its content.
- A stale artifact from read_file must not be treated as the current file contents.
"""


def render_prefix(workspace: "Workspace", registry: "ToolRegistry") -> str:
    sections = [
        SYSTEM_RULES.strip(),
        OUTPUT_PROTOCOL.strip(),
        render_tool_definitions(registry),
        render_project_rules(workspace),
        STABLE_SAFETY_RULES.strip(),
    ]
    return "\n\n".join(section for section in sections if section.strip())


def render_tool_definitions(registry: "ToolRegistry") -> str:
    lines = [
        "Tool definitions:",
        "se only the tools listed below. Do not invent tool names.",
        "",
        "Available tools:",
    ]
    for name in sorted(registry.tools):
        tool = registry.tools[name]
        schema = _schema_for_prompt(tool.schema)
        lines.extend(
            [
                f"- {tool.name}: {tool.description or '(no description)'}",
                f"  read_only: {_bool_text(tool.read_only)}",
                f"  risky: {_bool_text(tool.risky)}",
                f"  args_schema: {schema}",
            ]
        )
    return "\n".join(lines)


def render_project_rules(workspace: "Workspace") -> str:
    path = workspace.root / PROJECT_RULES_FILE
    if not path.is_file():
        return "Project rules from JCODE.md:\n(none)"
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) > PROJECT_RULES_MAX_CHARS:
        text = text[:PROJECT_RULES_MAX_CHARS].rstrip() + "\n\n[truncated]"
    return "Project rules from JCODE.md:\n" + (text or "(empty)")


def _schema_for_prompt(schema_type: type) -> str:
    if hasattr(schema_type, "model_json_schema"):
        schema = schema_type.model_json_schema()
    else:
        schema = {}
    compact = {
        "type": schema.get("type", "object"),
        "properties": schema.get("properties", {}),
        "required": schema.get("required", []),
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"
