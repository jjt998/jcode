from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from collections import OrderedDict

from src.context.budget import estimate_tokens, tail_clip
from src.context.skills import render_skill_section
from src.runtime.plan import render_runtime_mode_text
from src.state.workspace import now_iso


SECTION_ORDER = ("prefix", "skill", "working_memory", "history", "current_request")
CURRENT_REQUEST_SECTION = "current_request"
MIN_SECTION_BUDGETS = {
    "prefix": 4000,
    "skill": 600,
    "working_memory": 2200,
    "history": 6000,
}
REDUCTION_ORDER = ("working_memory", "skill", "history")
SECTION_RATIO_HINTS = {
    "prefix": 0.20,
    "skill": 0.07,
    "working_memory": 0.23,
    "history": 0.50,
}

PRESSURE_LEVELS = (
    (0.60, 0.70, 1, "60-70"),
    (0.70, 0.80, 2, "70-80"),
    (0.80, 0.95, 3, "80-95"),
    (0.95, 10.0, 4, "95+"),
)


def compute_section_budgets(total_budget_chars: int, ratios: dict | None = None) -> dict:
    ratios = ratios or SECTION_RATIO_HINTS
    floor_sum = sum(MIN_SECTION_BUDGETS.get(section, 0) for section in ratios)
    budgets = {}
    if total_budget_chars < floor_sum:
        for section, ratio in ratios.items():
            budgets[section] = int(total_budget_chars * ratio)
        return budgets
    for section, ratio in ratios.items():
        floor = int(MIN_SECTION_BUDGETS.get(section, 0))
        budgets[section] = max(floor, int(total_budget_chars * ratio))
    return budgets


@dataclass
class ContextBuildResult:
    context: str
    ctx_info: dict
    should_compact: bool = False
    compact_trigger: str | None = None


@dataclass
class _SectionRender:
    raw: str
    rendered: str
    budget_chars: int | None
    details: dict | None = None

    @property
    def raw_chars(self) -> int:
        return len(self.raw)

    @property
    def rendered_chars(self) -> int:
        return len(self.rendered)


class ContextManager:
    workspace: object
    durable_memory: object
    registry: object
    total_budget: int

    def __init__(self, workspace, durable_memory, registry, total_budget: int = 60000):
        self.workspace = workspace
        self.durable_memory = durable_memory
        self.registry = registry
        self.total_budget = int(total_budget)

    def build(self, session: dict, working_memory, user_message: str) -> ContextBuildResult:
        user_message = str(user_message)
        self._sync_compact_summary_from_history(session, working_memory)

        section_texts = self._build_section_texts(session, working_memory, user_message)
        budgets = self._base_budgets()

        rendered, ctx_info = self._render_with_budget_plan(
            session=session,
            working_memory=working_memory,
            section_texts=section_texts,
            budgets=budgets,
        )
        context = self._assemble_context(rendered)
        total_chars = len(context)
        total_estimated_tokens = estimate_tokens(context)

        pressure = self._build_pressure(total_estimated_tokens, ctx_info["budget"]["total_budget_tokens"])
        budget_info = ctx_info["budget"]
        budget_info["total_chars"] = total_chars
        budget_info["total_estimated_tokens"] = total_estimated_tokens
        budget_info["pressure_ratio"] = pressure["ratio"]
        budget_info["pressure_level"] = pressure["level"]

        cache_info = self._build_cache_info(session, rendered["prefix"].rendered)
        ctx_info["pressure"] = pressure
        ctx_info["cache"] = cache_info
        ctx_info["workspace"]["workspace_hash"] = cache_info["workspace_hash"]
        ctx_info["prefix"]["hash"] = cache_info["prefix_hash"]
        ctx_info["prefix"]["cache_key"] = cache_info["prompt_cache_key"]
        ctx_info["history"]["compact_summary"] = working_memory.compact_summary
        ctx_info["memory"] = {
            "retrieval": {
                "enabled": False,
                "items": [],
                "query": user_message,
            },
            "compact_summary": working_memory.compact_summary,
        }
        ctx_info["compact"]["eligible"] = pressure["level"] == 4
        ctx_info["compact"]["trigger"] = "semantic_summary" if pressure["level"] == 4 else ""
        ctx_info["compact"]["should_compact"] = pressure["level"] == 4
        ctx_info["compact"]["status"] = "pending" if pressure["level"] == 4 else "idle"

        session["ctx_info"] = ctx_info
        session["ctx_info"]["cache"] = cache_info

        return ContextBuildResult(
            context=context,
            ctx_info=ctx_info,
            should_compact=bool(pressure["level"] == 4),
            compact_trigger="semantic_summary" if pressure["level"] == 4 else None,
        )

    def compact_history(self, session: dict, working_memory, *, retain_turns: int = 2, summary_mode: str = "deterministic") -> dict:
        history = [item for item in session.get("history", []) if item.get("kind") != "compact_summary"]
        turns = self._group_turns(history)
        ordered_turn_ids = list(turns)
        if not ordered_turn_ids:
            return {
                "status": "noop",
                "summary_mode": summary_mode,
                "retain_turns": int(retain_turns),
                "before": {"turn_count": 0, "item_count": 0},
                "after": {"turn_count": 0, "item_count": 0},
            }

        retain_turns = max(1, int(retain_turns))
        keep_turn_ids = ordered_turn_ids[-retain_turns:]
        kept_items = []
        compacted_items = []
        for turn_id, items in turns.items():
            if turn_id in keep_turn_ids:
                kept_items.extend(items)
            else:
                compacted_items.extend(items)

        before_text = self._render_history_text(history, recent_turn_window=max(2, retain_turns), clip_old_tools=False)
        summary_text = self._summarize_compacted_history(compacted_items, session=session, summary_mode=summary_mode)
        session["event_seq"] = int(session.get("event_seq", 0)) + 1
        summary_item = {
            "role": "system",
            "kind": "compact_summary",
            "content": summary_text,
            "event_id": f"event-{session['event_seq']:06d}",
            "created_at": now_iso(),
            "source": "context_manager",
            "run_id": keep_turn_ids[-1] if keep_turn_ids else "",
            "turn_id": f"compact-{session['event_seq']:06d}",
        }
        session["history"] = [summary_item, *kept_items]
        working_memory.set_compact_summary(summary_text)

        after_text = self._render_history_text(session["history"], recent_turn_window=max(2, retain_turns), clip_old_tools=False)
        ctx_info = {
            "compact": {
                "status": "applied",
                "mode": "semantic",
                "summary_mode": summary_mode,
                "retain_turns": retain_turns,
                "before": {
                    "turn_count": len(ordered_turn_ids),
                    "item_count": len(history),
                    "rendered_chars": len(before_text),
                    "text": before_text,
                },
                "after": {
                    "turn_count": len(self._group_turns(session["history"])),
                    "item_count": len(session["history"]),
                    "rendered_chars": len(after_text),
                    "text": after_text,
                },
                "summary_item": summary_item,
                "summary_text": summary_text,
            }
        }
        session["ctx_info"] = dict(session.get("ctx_info", {}), **ctx_info)
        return ctx_info["compact"]

    def _render_with_budget_plan(self, *, session: dict, working_memory, section_texts: dict, budgets: dict) -> tuple[dict[str, _SectionRender], dict]:
        pressure_level = 0
        history_windows = [5]
        candidate_budgets = dict(budgets)

        rendered = self._render_sections(section_texts, session, working_memory, candidate_budgets, history_windows[0])
        prompt = self._assemble_context(rendered)
        pressure = self._build_pressure(estimate_tokens(prompt), self._budget_tokens())
        pressure_level = pressure["level"]

        if pressure_level == 1:
            history_windows = [5, 3]
            rendered = self._render_sections(section_texts, session, working_memory, candidate_budgets, history_windows[-1])
            prompt = self._assemble_context(rendered)
            pressure = self._build_pressure(estimate_tokens(prompt), self._budget_tokens())
            pressure_level = pressure["level"]

        if pressure_level >= 2:
            candidate_budgets["working_memory"] = max(
                MIN_SECTION_BUDGETS["working_memory"],
                int(candidate_budgets["working_memory"] * 0.7),
            )
            history_windows = [2]
            rendered = self._render_sections(section_texts, session, working_memory, candidate_budgets, history_windows[0])
            prompt = self._assemble_context(rendered)
            pressure = self._build_pressure(estimate_tokens(prompt), self._budget_tokens())
            pressure_level = pressure["level"]

        if pressure_level >= 3:
            candidate_budgets["skill"] = max(
                MIN_SECTION_BUDGETS["skill"],
                int(candidate_budgets["skill"] * 0.5),
            )
            rendered = self._render_sections(section_texts, session, working_memory, candidate_budgets, 2)
            prompt = self._assemble_context(rendered)
            pressure = self._build_pressure(estimate_tokens(prompt), self._budget_tokens())
            pressure_level = pressure["level"]

        reductions = []
        while len(prompt) > self.total_budget:
            overflow = len(prompt) - self.total_budget
            reduced = False
            for section in REDUCTION_ORDER:
                current_budget = int(candidate_budgets.get(section, 0))
                floor = int(MIN_SECTION_BUDGETS.get(section, 0))
                if current_budget <= floor:
                    continue
                new_budget = max(floor, current_budget - overflow)
                if new_budget >= current_budget:
                    continue
                candidate_budgets[section] = new_budget
                reductions.append(
                    {
                        "section": section,
                        "before_chars": current_budget,
                        "after_chars": new_budget,
                        "overflow_chars": overflow,
                    }
                )
                rendered = self._render_sections(section_texts, session, working_memory, candidate_budgets, 2 if pressure_level >= 2 else 5)
                prompt = self._assemble_context(rendered)
                reduced = True
                break
            if not reduced:
                break

        ctx_info = {
            "workspace": self._workspace_info(),
            "prefix": {
                "hash": "",
                "cache_key": "",
                "sections": {},
            },
            "budget": {
                "total_budget_chars": self.total_budget,
                "total_budget_tokens": self._budget_tokens(),
                "section_ratios": dict(SECTION_RATIO_HINTS),
                "section_order": list(SECTION_ORDER),
                "section_budgets": {
                    section: (None if section == CURRENT_REQUEST_SECTION else int(candidate_budgets.get(section, 0)))
                    for section in SECTION_ORDER
                },
                "sections": {},
                "reductions": reductions,
            },
            "pressure": {
                "ratio": 0.0,
                "level": 0,
                "tier": "tier0_observe",
                "range": "0-60",
                "recent_turn_window": 5,
            },
            "history": {
                "turn_count": 0,
                "rendered_turn_count": 0,
                "recent_turn_window": 5,
                "turns": [],
                "compact_summary": working_memory.compact_summary,
            },
            "compact": {
                "status": "idle",
                "mode": "none",
                "eligible": False,
                "should_compact": False,
                "trigger": "",
                "retain_turns": 2,
                "before": {},
                "after": {},
            },
            "cache": {},
            "memory": {},
        }
        for name, render in rendered.items():
            ctx_info["budget"]["sections"][name] = {
                "raw_chars": render.raw_chars,
                "rendered_chars": render.rendered_chars,
                "budget_chars": render.budget_chars,
            }
        ctx_info["prefix"]["sections"] = {
            key: value for key, value in ctx_info["budget"]["sections"].items() if key in {"prefix", "skill"}
        }
        filtered_history = [item for item in session.get("history", []) if item.get("kind") != "compact_summary"]
        ctx_info["history"] = {
            **ctx_info["history"],
            "turn_count": len(self._group_turns(filtered_history)),
            "rendered_turn_count": rendered["history"].details.get("rendered_turn_count", 0) if rendered["history"].details else 0,
            "recent_turn_window": rendered["history"].details.get("recent_turn_window", 5) if rendered["history"].details else 5,
            "turns": rendered["history"].details.get("turns", []) if rendered["history"].details else [],
        }
        return rendered, ctx_info

    def _render_sections(self, section_texts: dict, session: dict, working_memory, budgets: dict, recent_turn_window: int) -> dict[str, _SectionRender]:
        rendered: dict[str, _SectionRender] = {}
        history_render = self._render_history_section(
            session,
            recent_turn_window=recent_turn_window,
            budget_chars=int(budgets.get("history", self._base_budgets()["history"])),
        )
        for section in SECTION_ORDER:
            if section == "prefix":
                raw = section_texts[section]
                budget_chars = int(budgets.get(section, 0))
                rendered[section] = _SectionRender(raw=raw, rendered=tail_clip(raw, budget_chars), budget_chars=budget_chars, details={})
            elif section == "skill":
                raw = section_texts[section]
                budget_chars = int(budgets.get(section, 0))
                rendered_text = tail_clip(raw, budget_chars)
                rendered[section] = _SectionRender(raw=raw, rendered=rendered_text, budget_chars=budget_chars, details={})
            elif section == "working_memory":
                raw = section_texts[section]
                budget_chars = int(budgets.get(section, 0))
                rendered_text = tail_clip(raw, budget_chars)
                rendered[section] = _SectionRender(raw=raw, rendered=rendered_text, budget_chars=budget_chars, details={})
            elif section == "history":
                rendered[section] = history_render
            else:
                raw = section_texts[section]
                rendered[section] = _SectionRender(raw=raw, rendered=raw, budget_chars=None, details={})
        return rendered

    def _render_history_section(self, session: dict, *, recent_turn_window: int, budget_chars: int) -> _SectionRender:
        history = [item for item in session.get("history", []) if item.get("kind") != "compact_summary"]
        raw = self._render_history_text(history, recent_turn_window=recent_turn_window, clip_old_tools=True)
        turns = self._group_turns(history)
        details = {
            "turns": [
                {"turn_id": turn_id, "item_count": len(items)}
                for turn_id, items in turns.items()
            ],
            "turn_count": len(turns),
            "rendered_turn_count": min(len(turns), recent_turn_window),
            "recent_turn_window": recent_turn_window,
        }
        return _SectionRender(raw=raw, rendered=tail_clip(raw, budget_chars), budget_chars=budget_chars, details=details)

    def _render_history_text(self, history: list[dict], *, recent_turn_window: int, clip_old_tools: bool) -> str:
        turns = self._group_turns(history)
        if not turns:
            return "Transcript:\n- empty"
        recent_turn_ids = set(list(turns.keys())[-max(1, int(recent_turn_window)):])
        lines = ["Transcript:"]
        for turn_id, items in turns.items():
            lines.append(f"Turn {turn_id}:")
            for item in items:
                if item.get("kind") == "compact_summary":
                    continue
                if turn_id in recent_turn_ids:
                    lines.extend(self._render_history_item(item, 900))
                else:
                    if item.get("role") == "tool" and clip_old_tools:
                        lines.append(self._summarize_old_tool_item(item))
                    else:
                        lines.extend(self._render_history_item(item, 120))
        return "\n".join(lines)

    def _render_history_item(self, item: dict, line_limit: int) -> list[str]:
        if item.get("kind") == "compact_summary":
            return []
        if item.get("role") == "tool":
            prefix = f"[tool:{item.get('name', '')}] {json.dumps(item.get('args', {}), sort_keys=True)}"
            content = tail_clip(str(item.get("content", "")), max(20, line_limit))
            return [prefix, content]
        return [f"[{item.get('role', '')}] {tail_clip(str(item.get('content', '')), line_limit)}"]

    def _summarize_old_tool_item(self, item: dict) -> str:
        if item.get("name") == "run_shell":
            command = str(item.get("args", {}).get("command", "")).strip() or "shell"
            content = str(item.get("content", "")).splitlines()
            preview = " | ".join(line.strip() for line in content if line.strip())[:200]
            return f"{command} -> {preview or '(empty)'}"
        if item.get("name") in {"write_file", "patch_file"}:
            path = str(item.get("args", {}).get("path", "")).strip() or "workspace"
            return f"{item.get('name', 'tool')} -> {path}"
        content = tail_clip(str(item.get("content", "")), 120)
        return f"{item.get('name', 'tool')} -> {content}"

    def _summarize_compacted_history(self, items: list[dict], *, session: dict, summary_mode: str) -> str:
        if summary_mode == "llm":
            summary_mode = "deterministic"
        goal = self._latest_user_message(items) or self._latest_user_message(session.get("history", [])) or "Continue the current task."
        constraints = self._collect_sentences(items, ("must", "only", "keep", "cannot", "don't", "do not", "avoid", "preserve", "不能", "必须", "只", "保持"))
        files_read = self._collect_paths(items, "read_file")
        files_modified = self._collect_paths(items, "write_file", "patch_file")
        key_decisions = self._collect_sentences(items, ("decide", "choose", "switch", "use", "改用", "选择", "决定"))
        blockers = self._collect_blockers(items)
        next_steps = self._collect_next_steps(items) or ["Continue from the latest preserved turn."]
        lines = ["## Goal", goal]
        if constraints:
            lines.extend(["", "## Constraints", *[f"- {item}" for item in constraints]])
        if files_read:
            lines.extend(["", "## Files Read", *[f"- {item}" for item in files_read]])
        if files_modified:
            lines.extend(["", "## Files Modified", *[f"- {item}" for item in files_modified]])
        if key_decisions:
            lines.extend(["", "## Key Decisions", *[f"- {item}" for item in key_decisions]])
        if blockers:
            lines.extend(["", "## Blockers", *[f"- {item}" for item in blockers]])
        lines.extend(["", "## Next Steps", *[f"- {item}" for item in next_steps]])
        return "\n".join(lines).strip()

    def _collect_sentences(self, items: list[dict], patterns: tuple[str, ...]) -> list[str]:
        found: list[str] = []
        lowered_patterns = tuple(pattern.lower() for pattern in patterns)
        for item in items:
            if item.get("role") not in {"user", "assistant", "tool"}:
                continue
            text = str(item.get("content", "")).strip()
            if not text:
                continue
            for sentence in re.split(r"[。！？!?]+|\n+|\.(?:\s+|$)", text):
                value = sentence.strip(" \t\r\n:;,.，；、")
                if not value:
                    continue
                lowered = value.lower()
                if any(pattern in lowered for pattern in lowered_patterns):
                    if value not in found:
                        found.append(value)
        return found[:6]

    def _collect_paths(self, items: list[dict], *names: str) -> list[str]:
        result: list[str] = []
        wanted = {name for name in names}
        for item in items:
            if item.get("name") not in wanted:
                continue
            path = str(item.get("args", {}).get("path", "")).strip()
            if path and path not in result:
                result.append(path)
        return result[:10]

    def _collect_blockers(self, items: list[dict]) -> list[str]:
        blockers: list[str] = []
        for item in items:
            status = str(item.get("tool_status", "")).strip()
            if item.get("role") == "tool" and status and status != "success":
                text = str(item.get("content", "")).strip()
                if text and text not in blockers:
                    blockers.append(text)
            if item.get("role") == "assistant":
                text = str(item.get("content", "")).strip()
                if any(marker in text.lower() for marker in ("blocked", "unable", "cannot", "failed")) and text not in blockers:
                    blockers.append(text)
        return blockers[:5]

    def _collect_next_steps(self, items: list[dict]) -> list[str]:
        for item in reversed(items):
            if item.get("role") != "assistant":
                continue
            text = str(item.get("content", "")).strip()
            if text:
                for sentence in re.split(r"[。！？!?]+|\n+|\.(?:\s+|$)", text):
                    value = sentence.strip(" \t\r\n:;,.，；、")
                    if value and any(marker in value.lower() for marker in ("next", "continue", "then", "之后", "接着", "继续")):
                        return [value]
        return []

    def _latest_user_message(self, items: list[dict]) -> str:
        for item in reversed(items):
            if item.get("role") == "user":
                text = str(item.get("content", "")).strip()
                if text:
                    return text
        return ""

    def _group_turns(self, items: list[dict]) -> OrderedDict[str, list[dict]]:
        turns: OrderedDict[str, list[dict]] = OrderedDict()
        for index, item in enumerate(items):
            turn_id = str(item.get("turn_id") or item.get("run_id") or f"legacy-{index:06d}")
            turns.setdefault(turn_id, []).append(item)
        return turns

    def _sync_compact_summary_from_history(self, session: dict, working_memory) -> None:
        for item in reversed(session.get("history", [])):
            if item.get("kind") == "compact_summary":
                working_memory.set_compact_summary(str(item.get("content", "")).strip())
                return

    def _build_section_texts(self, session: dict, working_memory, user_message: str) -> dict:
        return {
            "prefix": self._render_prefix(),
            "skill": render_skill_section(),
            "working_memory": self._render_working_memory(session, working_memory),
            "history": "",
            CURRENT_REQUEST_SECTION: f"Current user request:\n{user_message}",
        }

    def _render_prefix(self) -> str:
        sections = [
            "System rules:\n- You are JCode, a compact local coding agent.",
            "Output protocol:\n- To call a tool, return exactly: <tool name=\"tool_name\">{\"arg\": \"value\"}</tool>\n- To finish, return exactly: <final>answer</final>",
            self._render_tool_definitions(),
            self.workspace.project_rules_text(),
            self.workspace.stable_docs_text(),
            "Stable safety rules:\n- Stay inside the workspace.\n- Read files before writing them.\n- Do not repeat identical tool calls.\n- Shell and write actions may require approval and sandbox checks.\n- Summarize evidence from tools before finalizing.",
        ]
        return "\n\n".join(section for section in sections if str(section).strip())

    def _render_tool_definitions(self) -> str:
        lines = [
            "Tool definitions:",
            "Use only the tools listed below. Do not invent tool names.",
            "",
            "Available tools:",
        ]
        for name in sorted(getattr(self.registry, "tools", {})):
            tool = self.registry.tools[name]
            schema = self._schema_for_prompt(tool.schema)
            lines.extend(
                [
                    f"- {tool.name}: {tool.description or '(no description)'}",
                    f"  read_only: {'true' if tool.read_only else 'false'}",
                    f"  risky: {'true' if tool.risky else 'false'}",
                    f"  args_schema: {schema}",
                ]
            )
        return "\n".join(lines)

    def _schema_for_prompt(self, schema_type: type) -> str:
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

    def _render_working_memory(self, session: dict, working_memory) -> str:
        text = working_memory.render() + f"\n- workspace_root: {self.workspace.root}"
        runtime_mode_text = render_runtime_mode_text(session)
        if runtime_mode_text:
            text += "\n" + runtime_mode_text
        text += "\n" + self.workspace.runtime_text()
        return text

    def _assemble_context(self, rendered: dict[str, _SectionRender]) -> str:
        return "\n\n".join(rendered[section].rendered for section in SECTION_ORDER).strip()

    def _build_pressure(self, input_tokens: int, budget_tokens: int) -> dict:
        ratio = round(max(0, int(input_tokens)) / max(1, int(budget_tokens)), 4)
        level, range_text, tier = self._pressure_level(ratio)
        return {
            "ratio": ratio,
            "level": level,
            "tier": tier,
            "range": range_text,
            "source": "estimated",
            "input_tokens": int(input_tokens),
            "budget_tokens": int(budget_tokens),
        }

    def _pressure_level(self, ratio: float) -> tuple[int, str, str]:
        if ratio < 0.60:
            return 0, "0-60", "tier0"
        for low, high, level, label in PRESSURE_LEVELS:
            if low <= ratio < high:
                return level, label, f"tier{level}"
        return 4, "95+", "tier4"

    def _budget_tokens(self) -> int:
        return max(1, (int(self.total_budget) + 3) // 4)

    def _base_budgets(self) -> dict:
        return compute_section_budgets(self.total_budget, ratios=SECTION_RATIO_HINTS)

    def _workspace_info(self) -> dict:
        docs = []
        for path, snippet in getattr(self.workspace, "project_docs", {}).items():
            docs.append({"path": path, "chars": len(snippet)})
        return {
            "cwd": str(getattr(self.workspace, "cwd", getattr(self.workspace, "root", ""))),
            "repo_root": str(getattr(self.workspace, "repo_root", getattr(self.workspace, "root", ""))),
            "branch": str(getattr(self.workspace, "branch", "-")),
            "default_branch": str(getattr(self.workspace, "default_branch", "main")),
            "status": str(getattr(self.workspace, "status", "clean")),
            "recent_commits": list(getattr(self.workspace, "recent_commits", [])),
            "docs": docs,
            "workspace_hash": getattr(self.workspace, "workspace_hash", lambda: "")(),
        }

    def _build_cache_info(self, session: dict, prefix_text: str) -> dict:
        previous = dict(session.get("ctx_info", {}) or {})
        previous_cache = dict(previous.get("cache", {}) or {})
        prefix_hash = hashlib.sha256(str(prefix_text or "").encode("utf-8")).hexdigest()
        workspace_hash = getattr(self.workspace, "workspace_hash", lambda: "")()
        cache = {
            "prefix_hash": prefix_hash,
            "prompt_cache_key": prefix_hash,
            "workspace_hash": workspace_hash,
            "prefix_changed": bool(previous_cache.get("prefix_hash") and previous_cache.get("prefix_hash") != prefix_hash),
            "workspace_changed": bool(previous_cache.get("workspace_hash") and previous_cache.get("workspace_hash") != workspace_hash),
            "previous_prefix_hash": str(previous_cache.get("prefix_hash", "")),
            "previous_workspace_hash": str(previous_cache.get("workspace_hash", "")),
        }
        return cache
