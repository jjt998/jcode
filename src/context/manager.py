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
from src.providers.router import ModelRouter


SECTION_ORDER = ("prefix", "skill", "working_memory", "history", "current_request")
CURRENT_REQUEST_SECTION = "current_request"
MIN_SECTION_BUDGETS = {
    "prefix": 40000,
    "skill": 6000,
    "working_memory": 35000,
    "history": 60000,
}
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
    compact_audit: dict | None = None


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
    model_router: ModelRouter | None
    total_budget: int

    def __init__(self, workspace, durable_memory, registry, model_router: ModelRouter | None = None, total_budget: int = 60000):
        self.workspace = workspace
        self.durable_memory = durable_memory
        self.registry = registry
        self.model_router = model_router
        self.total_budget = int(total_budget)

    def build(self, session: dict, working_memory, user_message: str) -> ContextBuildResult:
        user_message = str(user_message)
        self._sync_compact_summary_from_history(session, working_memory)

        initial_rendered = self._build_sections_texts(
            session=session,
            working_memory=working_memory,
            user_message=user_message,
            budgets=self._base_budgets(),
            recent_turn_window=5,
            compress_old_tools=True,
            include_older_turns=True,
        )
        initial_section_texts = {section: initial_rendered[section].rendered for section in SECTION_ORDER}
        initial_prompt = self._build_prompt(initial_section_texts)
        initial_pressure = self._build_pressure(initial_section_texts, self._budget_tokens())

        compressed_section_texts, compression_info, compact_audit = self._compress_section_texts_by_pressure(
            session=session,
            working_memory=working_memory,
            user_message=user_message,
            section_texts=initial_section_texts,
            pressure=initial_pressure,
        )
        final_prompt = self._build_prompt(compressed_section_texts)
        final_pressure = self._build_pressure(compressed_section_texts, self._budget_tokens())
        cache_info = self._build_cache_info(session, compressed_section_texts["prefix"])
        ctx_info = self._build_ctx_info(
            session=session,
            working_memory=working_memory,
            user_message=user_message,
            initial_rendered=initial_rendered,
            compressed_section_texts=compressed_section_texts,
            initial_prompt=initial_prompt,
            final_prompt=final_prompt,
            initial_pressure=initial_pressure,
            final_pressure=final_pressure,
            compression_info=compression_info,
            cache_info=cache_info,
        )

        session["ctx_info"] = ctx_info

        return ContextBuildResult(
            context=final_prompt,
            ctx_info=ctx_info,
            should_compact=bool(compression_info.get("compact", {}).get("should_compact", False)),
            compact_trigger=str(compression_info.get("compact", {}).get("trigger", "") or "") or None,
            compact_audit=compact_audit,
        )

    def _build_prompt(self, section_texts: dict) -> str:
        return "\n\n".join(str(section_texts.get(section, "")).strip() for section in SECTION_ORDER).strip()

    def _build_pressure(self, section_texts: dict, budget_tokens: int) -> dict:
        prompt = self._build_prompt(section_texts)
        input_tokens = estimate_tokens(prompt)
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

    def _compress_section_texts_by_pressure(
        self,
        *,
        session: dict,
        working_memory,
        user_message: str,
        section_texts: dict,
        pressure: dict,
    ) -> tuple[dict, dict, dict | None]:
        level = int(pressure.get("level", 0))
        budgets = self._base_budgets()
        selected_budgets = dict(budgets)
        compressed_section_texts = dict(section_texts)
        history_details: dict = {}
        compact_audit = None
        compact_info = {
            "status": "idle",
            "mode": "none",
            "summary_mode": "",
            "summary_source": "",
            "retain_turns": 2,
            "before": {},
            "after": {},
            "summary_item": {},
            "summary_text": "",
            "trigger": "",
            "fallback_reason": "",
        }
        recent_turn_window = self._history_window_for_level(level)
        include_older_turns = level < 4

        match level:
            case 0:
                pass
            case 1:
                history_render = self._build_history_section_texts(
                    session,
                    recent_turn_window=3,
                    budget_chars=self._base_budgets()["history"],
                    compress_old_tools=True,
                    include_older_turns=True,
                )
                compressed_section_texts["history"] = history_render.rendered
                history_details = history_render.details or {}
            case 2:
                selected_budgets["skill"] = max(MIN_SECTION_BUDGETS["skill"], int(budgets["skill"] * 0.7))
                compressed_section_texts["skill"] = tail_clip(section_texts.get("skill", ""), selected_budgets["skill"])
                history_render = self._build_history_section_texts(
                    session,
                    recent_turn_window=2,
                    budget_chars=self._base_budgets()["history"],
                    compress_old_tools=True,
                    include_older_turns=True,
                )
                compressed_section_texts["history"] = history_render.rendered
                history_details = history_render.details or {}
                recent_turn_window = 2
            case 3:
                selected_budgets["skill"] = max(MIN_SECTION_BUDGETS["skill"], int(budgets["skill"] * 0.5))
                selected_budgets["working_memory"] = max(MIN_SECTION_BUDGETS["working_memory"], int(budgets["working_memory"] * 0.7))
                compressed_section_texts["skill"] = tail_clip(section_texts.get("skill", ""), selected_budgets["skill"])
                compressed_section_texts["working_memory"] = tail_clip(section_texts.get("working_memory", ""), selected_budgets["working_memory"])
                history_render = self._build_history_section_texts(
                    session,
                    recent_turn_window=2,
                    budget_chars=self._base_budgets()["history"],
                    compress_old_tools=True,
                    include_older_turns=True,
                )
                compressed_section_texts["history"] = history_render.rendered
                history_details = history_render.details or {}
                recent_turn_window = 2
            case 4:
                selected_budgets["skill"] = max(MIN_SECTION_BUDGETS["skill"], int(budgets["skill"] * 0.5))
                selected_budgets["working_memory"] = max(MIN_SECTION_BUDGETS["working_memory"], int(budgets["working_memory"] * 0.7))
                compact_info, compact_audit = self.compact_history(session, working_memory, retain_turns=2, summary_mode="llm")
                compact_info["trigger"] = "semantic_summary"
                compact_info["should_compact"] = True
                compact_info["eligible"] = True
                compressed_section_texts["skill"] = tail_clip(section_texts.get("skill", ""), selected_budgets["skill"])
                compressed_section_texts["working_memory"] = tail_clip(section_texts.get("working_memory", ""), selected_budgets["working_memory"])
                history_render = self._build_history_section_texts(
                    session,
                    recent_turn_window=2,
                    budget_chars=self._base_budgets()["history"],
                    compress_old_tools=True,
                    include_older_turns=False,
                )
                compressed_section_texts["history"] = history_render.rendered
                history_details = history_render.details or {}
                compact_info["history_render"] = {
                    "raw": history_render.raw,
                    "rendered": history_render.rendered,
                    "budget_chars": history_render.budget_chars,
                    "details": history_render.details or {},
                }
                recent_turn_window = 2
            case _:
                pass

        if "history_render" not in compact_info:
            history_render = self._build_history_section_texts(
                session,
                recent_turn_window=recent_turn_window,
                budget_chars=self._base_budgets()["history"],
                compress_old_tools=True,
                include_older_turns=include_older_turns,
            )
            compact_info["history_render"] = {
                "raw": history_render.raw,
                "rendered": history_render.rendered,
                "budget_chars": history_render.budget_chars,
                "details": history_render.details or {},
            }

        compression_records = self._build_compression_records(
            initial_section_texts=section_texts,
            compressed_section_texts=compressed_section_texts,
            initial_budgets=budgets,
            compressed_budgets=selected_budgets,
            history_details=history_details,
        )
        return compressed_section_texts, {
            "level": level,
            "recent_turn_window": recent_turn_window,
            "selected_budgets": selected_budgets,
            "initial_budgets": budgets,
            "compression_records": compression_records,
            "history_records": history_details.get("compression_records", []),
            "compact": compact_info,
        }, compact_audit

    def _build_ctx_info(
        self,
        *,
        session: dict,
        working_memory,
        user_message: str,
        initial_rendered: dict[str, _SectionRender],
        compressed_section_texts: dict,
        initial_prompt: str,
        final_prompt: str,
        initial_pressure: dict,
        final_pressure: dict,
        compression_info: dict,
        cache_info: dict,
    ) -> dict:
        initial_total_chars = len(initial_prompt)
        final_total_chars = len(final_prompt)
        initial_total_tokens = estimate_tokens(initial_prompt)
        final_total_tokens = estimate_tokens(final_prompt)
        selected_budgets = dict(compression_info.get("selected_budgets", {}))
        budget_sections = {
            section: (None if section == CURRENT_REQUEST_SECTION else int(selected_budgets.get(section, 0)))
            for section in SECTION_ORDER
        }
        initial_budget_sections = {
            section: (None if section == CURRENT_REQUEST_SECTION else int(self._base_budgets().get(section, 0)))
            for section in SECTION_ORDER
        }
        history_render_info = dict(compression_info.get("compact", {}).get("history_render", {}) or {})
        history_render_raw = str(history_render_info.get("raw", initial_rendered["history"].raw))
        history_render_rendered = str(history_render_info.get("rendered", compressed_section_texts.get("history", "")))
        history_render_budget = history_render_info.get("budget_chars", initial_rendered["history"].budget_chars)
        history_render_details = dict(history_render_info.get("details", {}) or {})
        ctx_info = {
            "workspace": self._workspace_info(),
            "prefix": {
                "hash": cache_info["prefix_hash"],
                "cache_key": cache_info["prompt_cache_key"],
                "sections": {
                    key: value
                    for key, value in {
                        section: {
                            "raw_chars": initial_rendered[section].raw_chars if section != "history" else len(history_render_raw),
                            "rendered_chars": initial_rendered[section].rendered_chars if section != "history" else len(history_render_rendered),
                            "budget_chars": initial_rendered[section].budget_chars if section != "history" else history_render_budget,
                        }
                        for section in ("prefix", "skill")
                    }.items()
                },
            },
            "budget": {
                "total_budget_chars": self.total_budget,
                "total_budget_tokens": self._budget_tokens(),
                "section_ratios": dict(SECTION_RATIO_HINTS),
                "section_order": list(SECTION_ORDER),
                "initial_section_budgets": initial_budget_sections,
                "section_budgets": budget_sections,
                "sections": {
                    section: {
                        "raw_chars": initial_rendered[section].raw_chars if section != "history" else len(history_render_raw),
                        "rendered_chars": len(compressed_section_texts.get(section, "")) if section != "history" else len(history_render_rendered),
                        "budget_chars": selected_budgets.get(section, None) if section != CURRENT_REQUEST_SECTION else None,
                    }
                    for section in SECTION_ORDER
                },
                "total_chars": final_total_chars,
                "total_estimated_tokens": final_total_tokens,
                "initial_total_chars": initial_total_chars,
                "initial_total_estimated_tokens": initial_total_tokens,
            },
            "pressure": final_pressure,
            "pressure_initial": initial_pressure,
            "history": {
                "turn_count": len(self._group_turns([item for item in session.get("history", []) if item.get("kind") != "compact_summary"])),
                "rendered_turn_count": history_render_details.get("rendered_turn_count", 0),
                "recent_turn_window": history_render_details.get("recent_turn_window", compression_info.get("recent_turn_window", 5)),
                "turns": history_render_details.get("turns", []),
                "compact_summary": working_memory.compact_summary,
                "compression_records": history_render_details.get("compression_records", []),
            },
            "compact": compression_info.get("compact", {
                "status": "idle",
                "mode": "none",
                "eligible": False,
                "should_compact": False,
                "trigger": "",
                "summary_source": "",
                "fallback_reason": "",
                "retain_turns": 2,
                "before": {},
                "after": {},
                "summary_item": {},
                "summary_text": "",
            }),
            "cache": cache_info,
            "memory": {
                "retrieval": {
                    "enabled": False,
                    "items": [],
                    "query": user_message,
                },
                "compact_summary": working_memory.compact_summary,
            },
            "compression": {
                "initial": {
                "prompt_chars": initial_total_chars,
                "prompt_tokens": initial_total_tokens,
                "section_texts": {
                    section: {
                        "before_preview": self._preview_text(initial_rendered[section].rendered),
                        "after_preview": self._preview_text(compressed_section_texts.get(section, "")),
                        "before_chars": initial_rendered[section].rendered_chars,
                        "after_chars": len(compressed_section_texts.get(section, "")),
                    }
                    for section in SECTION_ORDER
                },
                },
                "final": {
                    "prompt_chars": final_total_chars,
                    "prompt_tokens": final_total_tokens,
                },
                "records": compression_info.get("compression_records", []),
            },
        }
        ctx_info["workspace"]["workspace_hash"] = cache_info["workspace_hash"]
        ctx_info["budget"]["section_diffs"] = compression_info.get("compression_records", [])
        ctx_info["budget"]["reductions"] = compression_info.get("compression_records", [])
        ctx_info["compact"]["eligible"] = bool(final_pressure.get("level", 0) == 4 or compression_info.get("compact", {}).get("status") == "applied")
        ctx_info["compact"]["should_compact"] = bool(compression_info.get("compact", {}).get("should_compact", False))
        ctx_info["compact"]["trigger"] = str(compression_info.get("compact", {}).get("trigger", "") or "")
        ctx_info["compact"]["status"] = str(compression_info.get("compact", {}).get("status", "idle"))
        ctx_info["compact"]["summary_source"] = str(compression_info.get("compact", {}).get("summary_source", ""))
        ctx_info["compact"]["fallback_reason"] = str(compression_info.get("compact", {}).get("fallback_reason", ""))
        return ctx_info

    def compact_history(self, session: dict, working_memory, *, retain_turns: int = 2, summary_mode: str = "llm") -> tuple[dict, dict]:
        '''
        第四层做语义压缩，这一层会真正压缩历史结构，不是动渲染。否则会导致连续的语义压缩，成本巨高。
        '''
        history = [item for item in session.get("history", []) if item.get("kind") != "compact_summary"]
        turns = self._group_turns(history)
        ordered_turn_ids = list(turns)
        if not ordered_turn_ids:
            compact_info = {
                "status": "noop",
                "summary_mode": summary_mode,
                "summary_source": "",
                "retain_turns": int(retain_turns),
                "before": {"turn_count": 0, "item_count": 0},
                "after": {"turn_count": 0, "item_count": 0},
            }
            return compact_info, None

        retain_turns = max(1, int(retain_turns))
        keep_turn_ids = ordered_turn_ids[-retain_turns:]
        kept_items = []
        compacted_items = []
        for turn_id, items in turns.items():
            if turn_id in keep_turn_ids:
                kept_items.extend(items)
            else:
                compacted_items.extend(items)

        before_text, _ = self._build_history_text(
            history,
            recent_turn_window=max(2, retain_turns),
            compress_old_tools=False,
            include_older_turns=True,
        )
        summary_text, summary_audit = self._summarize_compacted_history(compacted_items, session=session, summary_mode=summary_mode)
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

        after_text, _ = self._build_history_text(
            session["history"],
            recent_turn_window=max(2, retain_turns),
            compress_old_tools=False,
            include_older_turns=True,
        )
        ctx_info = {
            "compact": {
                "status": "applied",
                "mode": "semantic",
                "summary_mode": summary_mode,
                "summary_source": str(summary_audit.get("source", "deterministic")),
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
                "fallback_reason": str(summary_audit.get("fallback_reason", "")),
            }
        }
        session["ctx_info"] = dict(session.get("ctx_info", {}), **ctx_info)
        return ctx_info["compact"], summary_audit

    def _build_sections_texts(
        self,
        *,
        session: dict,
        working_memory,
        user_message: str,
        budgets: dict,
        recent_turn_window: int,
        compress_old_tools: bool,
        include_older_turns: bool,
    ) -> dict[str, _SectionRender]:
        rendered: dict[str, _SectionRender] = {}
        history_render = self._build_history_section_texts(
            session,
            recent_turn_window=recent_turn_window,
            budget_chars=int(budgets.get("history", self._base_budgets()["history"])),
            compress_old_tools=compress_old_tools,
            include_older_turns=include_older_turns,
        )
        section_texts = {
            "prefix": self._build_prefix_text(),
            "skill": render_skill_section(),
            "working_memory": self._build_working_memory_text(session, working_memory),
            "history": history_render.raw,
            CURRENT_REQUEST_SECTION: f"Current user request:\n{user_message}",
        }
        for section in SECTION_ORDER:
            if section == "prefix":
                raw = section_texts[section]
                rendered[section] = _SectionRender(raw=raw, rendered=raw, budget_chars=None, details={})
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

    def _build_history_section_texts(
        self,
        session: dict,
        *,
        recent_turn_window: int,
        budget_chars: int,
        compress_old_tools: bool,
        include_older_turns: bool, # 是否表示保留窗口外的历史，只有第四层不保存，因为要做旧历史的语义压缩。
    ) -> _SectionRender:
        history = [item for item in session.get("history", []) if item.get("kind") != "compact_summary"]
        raw, compression_records = self._build_history_text(
            history,
            recent_turn_window=recent_turn_window,
            compress_old_tools=compress_old_tools,
            include_older_turns=include_older_turns,
        )
        turns = self._group_turns(history)
        details = {
            "turns": [
                {"turn_id": turn_id, "item_count": len(items)}
                for turn_id, items in turns.items()
            ],
            "turn_count": len(turns),
            "rendered_turn_count": min(len(turns), recent_turn_window),
            "recent_turn_window": recent_turn_window,
            "compression_records": compression_records,
            "include_older_turns": include_older_turns,
        }
        rendered = "History:\n" + tail_clip(raw, budget_chars)
        return _SectionRender(raw=raw, rendered=rendered, budget_chars=budget_chars, details=details)

    def _build_history_text(
        self,
        history: list[dict],
        *,
        recent_turn_window: int,
        compress_old_tools: bool,
        include_older_turns: bool,
    ) -> tuple[str, list[dict]]:
        turns = self._group_turns(history)
        if not turns:
            return "Transcript:\n- empty", []
        recent_turn_ids = set(list(turns.keys())[-max(1, int(recent_turn_window)):])
        lines = ["Transcript:"]
        records: list[dict] = []
        seen_old_read_paths: set[str] = set()
        for turn_id, items in turns.items():
            if turn_id not in recent_turn_ids and not include_older_turns:
                continue
            turn_lines = self._build_turn_history_text(
                turn_id,
                items,
                compress_old_tools=compress_old_tools and turn_id not in recent_turn_ids,
                seen_old_read_paths=seen_old_read_paths,
                records=records,
            )
            if turn_lines:
                lines.extend(turn_lines)
        return "\n".join(lines), records

    def _build_history_item_text(self, item: dict, line_limit: int | None) -> list[str]:
        if item.get("kind") == "compact_summary":
            return []
        role = str(item.get("role", ""))
        if role == "tool":
            return self._render_tool_history_block(item, line_limit)
        if role == "assistant":
            return self._render_assistant_history_block(item, line_limit)
        if role == "user":
            content = str(item.get("content", ""))
            if line_limit is not None:
                content = tail_clip(content, max(20, int(line_limit)))
            return ["[User]", content]
        content = str(item.get("content", ""))
        if line_limit is not None:
            content = tail_clip(content, max(20, int(line_limit)))
        return [f"[{role or 'message'}]", content]

    def _build_turn_history_text(
        self,
        turn_id: str,
        items: list[dict],
        *,
        compress_old_tools: bool,
        seen_old_read_paths: set[str],
        records: list[dict],
    ) -> list[str]:
        lines = [f"--- Turn {turn_id} ---"]
        assistant_seen = False
        assistant_placeholder = False
        for item in items:
            if item.get("kind") == "compact_summary":
                continue
            role = str(item.get("role", ""))
            if role == "user":
                self._append_history_block(lines, self._build_history_item_text(item, None))
                continue
            if role == "assistant":
                self._append_history_block(lines, self._build_history_item_text(item, None))
                assistant_seen = True
                continue
            if role == "tool":
                if not assistant_seen and not assistant_placeholder:
                    self._append_history_block(lines, self._build_empty_assistant_block())
                    assistant_placeholder = True
                if compress_old_tools:
                    compressed_lines, record = self._compress_old_tool_history_item(item, seen_old_read_paths)
                    self._append_history_block(lines, compressed_lines)
                    if record:
                        records.append(record)
                else:
                    self._append_history_block(lines, self._build_history_item_text(item, None))
                continue
            self._append_history_block(lines, self._build_history_item_text(item, None))
        if not assistant_seen and not assistant_placeholder:
            self._append_history_block(lines, self._build_empty_assistant_block())
        return lines

    def _build_empty_assistant_block(self) -> list[str]:
        return ["[Assistant]", ""]

    def _append_history_block(self, lines: list[str], block: list[str]) -> None:
        if not block:
            return
        if len(lines) > 1 and lines[-1] != "":
            lines.append("")
        lines.extend(block)

    def _render_assistant_history_block(self, item: dict, line_limit: int | None) -> list[str]:
        action_kind = str(item.get("action_kind", "")).strip()
        reasoning = str(item.get("reasoning", "")).strip()
        content = str(item.get("content", ""))
        if line_limit is not None and action_kind == "final":
            content = tail_clip(content, max(20, int(line_limit)))
        lines = ["[Assistant]"]
        if reasoning:
            lines.append(f"<reasoning>{reasoning}</reasoning>")
        if action_kind == "final":
            lines.append(f"<final>{content}</final>")
        elif action_kind in {"tool", "tools"}:
            lines.append(content if content else "")
        elif action_kind == "invalid":
            lines.append(str(item.get("raw_content", "") or content))
        else:
            lines.append("")
        return lines

    def _render_tool_history_block(self, item: dict, line_limit: int | None) -> list[str]:
        name = str(item.get("name", ""))
        prefix = f"[ToolResult ({name})]<args>{self._tool_args_json(item)}</args>"
        if name == "read_file" and self._is_stale_read_file(item):
            return [prefix, self._stale_read_file_message(item)]
        content = str(item.get("content", ""))
        if line_limit is not None:
            content = tail_clip(content, max(20, int(line_limit)))
        return [prefix, content]

    def _compress_old_tool_history_item(self, item: dict, seen_old_read_paths: set[str]) -> tuple[list[str], dict | None]:
        name = str(item.get("name", ""))
        prefix = f"[ToolResult ({name})]<args>{self._tool_args_json(item)}</args>"
        content = str(item.get("content", ""))
        if not self._can_compress_tool_history_item(item):
            return self._build_history_item_text(item, None), None

        if name == "read_file" and self._is_stale_read_file(item):
            replacement = self._stale_read_file_message(item)
            record = self._compression_record_for_item(item, "stale_read_file_replaced", len(content), replacement)
            return [prefix, replacement], record

        artifact_path = self._artifact_path_from_content(content)
        if artifact_path:
            record = self._compression_record_for_item(item, "artifact_path_only", len(content), artifact_path)
            return [prefix, artifact_path], record

        if name == "read_file":
            path = str(item.get("args", {}).get("path", "")).strip()
            if path and path in seen_old_read_paths:
                replacement = f"[read_file:{path}] duplicate old read omitted"
                record = self._compression_record_for_item(item, "read_file_duplicate_omitted", len(content), replacement)
                return [prefix, replacement], record
            if path:
                seen_old_read_paths.add(path)
            return self._build_history_item_text(item, None), None

        if name == "run_shell":
            lines = self._run_shell_preview_lines(content)
            replacement = "\n".join(lines) if lines else "(empty)"
            record = self._compression_record_for_item(item, "run_shell_first_three_non_empty_lines", len(content), replacement)
            return [prefix, replacement], record

        replacement = content[:80]
        record = self._compression_record_for_item(item, "tool_first_80_chars", len(content), replacement)
        return [prefix, replacement], record

    @staticmethod
    def _is_stale_read_file(item: dict) -> bool:
        metadata = item.get("metadata", {})
        return item.get("name") == "read_file" and isinstance(metadata, dict) and bool(metadata.get("stale"))

    @staticmethod
    def _stale_read_file_message(item: dict) -> str:
        metadata = item.get("metadata", {})
        paths = metadata.get("stale_paths", []) if isinstance(metadata, dict) else []
        path_text = ", ".join(str(path) for path in paths if str(path).strip()) or str(item.get("args", {}).get("path", ""))
        return (
            f"This read_file result is stale because the source file changed: {path_text}. "
            "Treat it as historical evidence only and read the current file before relying on its content."
        )

    def _tool_args_json(self, item: dict) -> str:
        return json.dumps(item.get("args", {}) or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def _can_compress_tool_history_item(self, item: dict) -> bool:
        name = str(item.get("name", ""))
        status = str(item.get("tool_status", item.get("status", "")))
        if status and status not in {"success", "partial_success"}:
            return False
        if name in {
            "write_file",
            "apply_patch",
            "todo_add",
            "todo_update",
            "todo_list",
            "ask_user",
            "enter_plan_mode",
            "exit_plan_mode",
            "spawn_subagent",
            "send_subagent_message",
            "wait_subagent",
        }:
            return False
        return True

    def _artifact_path_from_content(self, content: str) -> str:
        for line in str(content).splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower().startswith("artifact"):
                match = re.search(r"(artifacts/[^\s]+)", stripped)
                if match:
                    return match.group(1)
                if ":" in stripped:
                    return stripped.split(":", 1)[1].strip()
                return stripped
            if stripped.startswith("artifacts/"):
                return stripped
        return ""

    def _run_shell_preview_lines(self, content: str) -> list[str]:
        lines: list[str] = []
        for raw_line in str(content).splitlines():
            line = raw_line.strip()
            if not line or line in {"stdout:", "stderr:", "exit_code:"}:
                continue
            lines.append(line)
            if len(lines) >= 3:
                break
        return lines

    def _compression_record_for_item(self, item: dict, rule: str, before_chars: int, after_text: str) -> dict:
        content = str(item.get("content", ""))
        return {
            "turn_id": str(item.get("turn_id", "")),
            "role": str(item.get("role", "")),
            "tool_name": str(item.get("name", "")),
            "rule": rule,
            "before_chars": int(before_chars),
            "after_chars": len(str(after_text)),
            "before_preview": self._preview_text(content),
            "after_preview": self._preview_text(after_text),
        }

    def _build_compression_records(
        self,
        *,
        initial_section_texts: dict,
        compressed_section_texts: dict,
        initial_budgets: dict,
        compressed_budgets: dict,
        history_details: dict,
    ) -> list[dict]:
        records: list[dict] = []
        for section in SECTION_ORDER:
            before = str(initial_section_texts.get(section, ""))
            after = str(compressed_section_texts.get(section, ""))
            record = {
                "section": section,
                "before_chars": len(before),
                "after_chars": len(after),
                "before_preview": self._preview_text(before),
                "after_preview": self._preview_text(after),
                "budget_before": None if section == CURRENT_REQUEST_SECTION else int(initial_budgets.get(section, 0)),
                "budget_after": None if section == CURRENT_REQUEST_SECTION else int(compressed_budgets.get(section, 0)),
                "changed": before != after,
            }
            if section == "history":
                record["tool_records"] = list(history_details.get("compression_records", []))
            records.append(record)
        return records

    def _history_window_for_level(self, level: int) -> int:
        if level <= 0:
            return 5
        if level == 1:
            return 3
        return 2

    def _preview_text(self, value: str, limit: int = 240) -> str:
        text = str(value or "").replace("\r\n", "\n").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + f"...[{len(text) - limit} chars]"

    def _summarize_compacted_history(self, items: list[dict], *, session: dict, summary_mode: str) -> tuple[str, dict]:
        if summary_mode == "llm":
            summary_text, audit = self._summarize_compacted_history_llm(items, session=session)
            if summary_text:
                return summary_text, audit
            summary_text = self._summarize_compacted_history_deterministic(items, session=session)
            audit = dict(audit or {})
            audit.update(
                {
                    "source": "deterministic",
                    "mode": "llm",
                    "status": "fallback",
                    "summary_text": summary_text,
                }
            )
            audit.setdefault("fallback_reason", "llm_summary_unavailable")
            return summary_text, audit
        summary_text = self._summarize_compacted_history_deterministic(items, session=session)
        return summary_text, {
            "source": "deterministic",
            "mode": summary_mode,
            "status": "success",
            "fallback_reason": "",
            "summary_text": summary_text,
            "prompt": "",
            "response": "",
        }

    def _summarize_compacted_history_deterministic(self, items: list[dict], *, session: dict) -> str:
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

    def _summarize_compacted_history_llm(self, items: list[dict], *, session: dict) -> tuple[str, dict]:
        if self.model_router is None:
            return "", {
                "source": "deterministic",
                "mode": "llm",
                "status": "fallback",
                "fallback_reason": "model_router_missing",
                "prompt": "",
                "response": "",
                "summary_text": "",
            }
        client = getattr(self.model_router, "client", None)
        if getattr(client, "api_key", "") == "":
            return "", {
                "source": "deterministic",
                "mode": "llm",
                "status": "fallback",
                "fallback_reason": "missing_api_key",
                "prompt": "",
                "response": "",
                "summary_text": "",
            }
        prompt = self._build_compact_summary_prompt(items, session=session)
        try:
            response = self.model_router.complete(prompt, max_tokens=900, temperature=0.0)
            text = str(response.text or "").strip()
            if not text:
                return "", {
                    "source": "deterministic",
                    "mode": "llm",
                    "status": "fallback",
                    "fallback_reason": "empty_llm_response",
                    "prompt": prompt,
                    "response": "",
                    "summary_text": "",
                }
            return text, {
                "source": "llm",
                "mode": "llm",
                "status": "success",
                "fallback_reason": "",
                "prompt": prompt,
                "response": text,
                "summary_text": text,
            }
        except Exception as exc:
            return "", {
                "source": "deterministic",
                "mode": "llm",
                "status": "fallback",
                "fallback_reason": f"{type(exc).__name__}: {exc}",
                "prompt": prompt,
                "response": "",
                "summary_text": "",
            }

    def _build_compact_summary_prompt(self, items: list[dict], *, session: dict) -> str:
        transcript = self._build_history_text(
            items,
            recent_turn_window=max(1, len(self._group_turns(items))),
            compress_old_tools=False,
            include_older_turns=True,
        )[0]
        return "\n".join(
            [
                "You are summarizing old context for a coding agent.",
                "Return Markdown only, with these sections in this exact order:",
                "## Goal",
                "## Constraints",
                "## Files Read",
                "## Files Modified",
                "## Key Decisions",
                "## Blockers",
                "## Next Steps",
                "Rules:",
                "- Be concise and factual.",
                "- Preserve file paths, constraints, decisions, blockers, and next steps.",
                "- If a section has no content, still include the heading with '- none'.",
                "- Do not mention that this is a summary prompt.",
                "",
                f"Current goal: {self._latest_user_message(session.get('history', [])) or 'Continue the current task.'}",
                "",
                "Transcript to summarize:",
                transcript,
            ]
        ).strip()

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

    def _build_prefix_text(self) -> str:
        # 这里把工具结果 artifact 约定直接写进系统前缀，明确告诉模型首行路径就是完整结果入口。
        sections = [
    "System rules:\n- You are JCode, a compact local coding agent.",
    (
        "Output protocol:\n"
        "- You must return exactly one protocol response per model response.\n"
        "- To include reasoning before your action, use <reasoning>...</reasoning> (optional). Only include it when the task is complex or you need to explain your thought process.\n"
        "- To call one tool, return exactly: <tool name=\"tool_name\">{\"arg\": \"value\"}</tool>\n"
        "- To call multiple tools in order, return exactly: <tools>[{\"name\": \"tool_name\", \"args\": {\"arg\": \"value\"}}]</tools>\n"
        "- To finish, return exactly: <final>answer</final>\n"
        "- Use only one primary protocol block per response: exactly one of <tool>, <tools>, or <final>.\n"
        "- If you include reasoning, it must be placed before the primary action block.\n"
        "- Do not output any natural language outside <reasoning>, <tool>, <tools>, or <final>.\n"
        "- Project/user style rules, such as required greetings, tone, or answer prefixes, must be applied inside <final>...</final> only.\n"
        "- When calling tools, do not satisfy style rules with plain text before or after the tool block.\n"
        "- If a style rule must be acknowledged before a tool call, put it inside <reasoning>...</reasoning>, not outside the protocol tags.\n"
        "- Any text outside the allowed protocol tags will be treated as invalid."
    ),
    self._build_tool_definitions_text(),
    self.workspace.project_rules_text(),
    self.workspace.stable_docs_text(),
    "Stable safety rules:\n- Stay inside the workspace.\n- Shell and write actions may require approval and sandbox checks.\n- Summarize evidence from tools before finalizing.\n- If a tool result starts with an artifacts/ path, treat that path as the full result artifact and read it when you need the complete output.",
]
        return "\n\n".join(section for section in sections if str(section).strip())

    def _build_tool_definitions_text(self) -> str:
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

    def _build_working_memory_text(self, session: dict, working_memory) -> str:
        text = working_memory.render() + f"\n- workspace_root: {self.workspace.root}"
        runtime_mode_text = render_runtime_mode_text(session)
        if runtime_mode_text:
            text += "\n" + runtime_mode_text
        text += "\n" + self.workspace.runtime_text()
        return text

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
