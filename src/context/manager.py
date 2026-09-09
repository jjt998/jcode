from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from src.context.budget import (
    MAX_CONTEXT_TOKENS,
    SAFETY_MARGIN,
    TokenizerAdapter,
    calculate_pressure,
    effective_window,
    validate_final_capacity,
    BudgetOccupancy,
)
from src.context.prefix import render_prefix
from src.context.result import ContextBuildOutcome, ContextResult, HistoryEvent
from src.context.skills import render_skill_section, select_skill_entries
from src.providers.request import compile_provider_input_snapshot
from src.runtime.errors import FinalContextExceedsWindowError, MandatoryContextExceedsWindowError, SystemMinimumBudgetOverflowError
from src.runtime.plan import render_runtime_mode_text
from src.context.summary import build_deterministic_summary, build_summary_request, call_summary_model


class ContextManager:
    """按 9.5 规则构建唯一、可审计的 Provider 输入。"""

    def __init__(self, workspace, durable_memory, registry, model_profile=None, actual_max_new_tokens: int = 16384, tokenizer: TokenizerAdapter | None = None, profile=None, summary_router=None, summary_config=None, **_legacy):
        self.workspace = workspace
        self.durable_memory = durable_memory
        self.registry = registry
        self.model_profile = model_profile or profile or SimpleNamespace(context_window_tokens=MAX_CONTEXT_TOKENS, max_output_tokens=actual_max_new_tokens)
        self.actual_max_new_tokens = int(actual_max_new_tokens)
        self.tokenizer = tokenizer or TokenizerAdapter()
        self.summary_router = summary_router
        self.summary_config = summary_config or SimpleNamespace(compact_summary_timeout_seconds=120, compact_summary_retry_count=2, compact_summary_initial_retry_delay_seconds=2, compact_summary_retry_multiplier=2, compact_summary_rebuild_on=True)

    def build(self, session: dict, working_memory, user_message: str, *, allowed_tools=None, provider_continuation: dict | None = None, run_store=None, run_dir=None) -> ContextBuildOutcome:
        """纯构建候选，不修改传入的活动 session 和 Working Memory。"""
        session_candidate = copy.deepcopy(session)
        memory_candidate = type(working_memory).from_dict(working_memory.to_dict(), self.workspace.root)
        original_goal = str(getattr(working_memory, "task_goal", "") or "")
        internal_instruction = str(user_message) if str(user_message).startswith("[Continuation Required]") else ""
        memory_candidate.task_goal = original_goal if internal_instruction else str(user_message)
        memory_candidate.sync_todos(session_candidate.get("todo_ledger", {}))
        runtime_text = getattr(self.workspace, "runtime_text", lambda: "")()
        memory_candidate.runtime_context = "\n".join(part for part in (render_runtime_mode_text(session_candidate), runtime_text) if part)
        history = [HistoryEvent.from_dict(item) for item in session_candidate.get("history", [])]
        original_history_items = [event.to_dict() for event in history]
        tools = self.registry.definitions(allowed_tools)
        prefix = render_prefix(self.workspace, self.registry)
        continuation = dict(provider_continuation or {})
        # 先构造完整输入测量固定占用和可变需求。
        full = ContextResult(prefix, render_skill_section(), history, memory_candidate, tools, {}, provider_continuation=continuation, internal_continuation_instruction=internal_instruction)
        full_snapshot = compile_provider_input_snapshot(full, self.tokenizer)
        turn_ids = [event.turn_id for event in history if event.turn_id]
        current_turn = turn_ids[-1] if turn_ids else ""
        fixed_history = [event for event in history if event.turn_id == current_turn] if current_turn else []
        fixed_memory = type(memory_candidate).from_dict({}, self.workspace.root)
        fixed_memory.task_goal = memory_candidate.task_goal
        fixed_context = ContextResult(prefix, "", fixed_history, fixed_memory, tools, {}, provider_continuation=continuation, internal_continuation_instruction=internal_instruction)
        fixed_snapshot = compile_provider_input_snapshot(fixed_context, self.tokenizer)
        fixed_tokens = fixed_snapshot.serialized_input_tokens
        window = effective_window(self.model_profile.context_window_tokens)
        fixed_demand = fixed_tokens + self.actual_max_new_tokens + SAFETY_MARGIN
        if fixed_demand > window:
            raise MandatoryContextExceedsWindowError(audit={"effective_context_window_tokens": window, "fixed_demand": fixed_demand})
        flexible = max(0, window - fixed_demand)
        # 压力必须基于 Provider 实际序列化输入，不能把 History 元数据等未发送内容计入需求。
        raw_flexible = max(0, full_snapshot.serialized_input_tokens - fixed_tokens)
        pressure = calculate_pressure(raw_flexible, flexible)
        level = int(pressure["level"])
        minimum_targets = {
            "skills": min(self.tokenizer.count(render_skill_section()), 512),
            "history": min(self.tokenizer.count(json.dumps([event.to_dict() for event in history], ensure_ascii=False)), 8192),
            "working_memory": min(self.tokenizer.count(memory_candidate.render()), 4096),
        }
        minimum_demand = fixed_demand + sum(minimum_targets.values())
        if minimum_demand > window and fixed_demand <= window and level < 4:
            raise SystemMinimumBudgetOverflowError(
                audit={"effective_context_window_tokens": window, "fixed_demand": fixed_demand, "minimum_targets": minimum_targets, "minimum_demand": minimum_demand}
            )
        history_artifact_ref = None
        session_commit_required = False
        compact_audit = None
        if level == 4 and run_store is not None and run_dir is not None and history:
            history_artifact_ref = run_store.write_history_artifact(run_dir, int(session_candidate.get("event_seq", 0)) + 1, [event.to_dict() for event in history])
            # 写入后立即重新读取并验签，后续摘要只允许使用这份只读证据。
            verified_history = [HistoryEvent.from_dict(item) for item in run_store.read_verified_artifact(history_artifact_ref)]
            ids = []
            for event in verified_history:
                if event.turn_id not in ids:
                    ids.append(event.turn_id)
            keep_ids = set(ids[-3:])
            evicted = [event.to_dict() for event in verified_history if event.turn_id not in keep_ids and event.kind != "compact_summary"]
            old_summary = None
            old_summary_event = None
            for event in reversed(verified_history):
                if event.kind == "compact_summary":
                    old_summary_event = event
                    try:
                        old_summary = json.loads(event.content)
                    except (TypeError, json.JSONDecodeError):
                        old_summary = None
                    break
            # 可选地从历史 artifact 链重建摘要输入，避免依赖已压缩正文。
            if bool(getattr(self.summary_config, "compact_summary_rebuild_on", True)) and old_summary_event:
                chain = old_summary_event.metadata.get("artifact_chain", []) if isinstance(old_summary_event.metadata, dict) else []
                rebuilt: dict[str, dict] = {}
                for ref in chain:
                    if not isinstance(ref, dict):
                        continue
                    for item in run_store.read_verified_artifact(ref):
                        event_id = str(item.get("event_id", ""))
                        if not event_id or item.get("kind") == "compact_summary" or str(item.get("turn_id") or "") in keep_ids:
                            continue
                        previous = rebuilt.get(event_id)
                        if previous is not None and previous != item:
                            from src.runtime.errors import ArtifactIntegrityError
                            raise ArtifactIntegrityError(f"history event conflict: {event_id}")
                        rebuilt.setdefault(event_id, item)
                if rebuilt:
                    # 旧摘要负责概括已压缩历史；artifact 只补充当前保留窗口之外的原始事件。
                    merged = dict(rebuilt)
                    for item in evicted:
                        event_id = str(item.get("event_id", ""))
                        if event_id:
                            merged.setdefault(event_id, item)
                    evicted = list(merged.values())
            summary = None
            summary_model_audit = None
            if self.summary_router is not None and evicted:
                summary_request = build_summary_request("Generate the fixed 9.5 compact summary JSON.", old_summary, evicted)
                summary, summary_model_audit = call_summary_model(
                    self.summary_router,
                    summary_request,
                    profile_id=str(getattr(self.model_profile, "id", "")),
                    profile=self.model_profile,
                    actual_max_new_tokens=self.actual_max_new_tokens,
                    tokenizer=self.tokenizer,
                    timeout_seconds=int(self.summary_config.compact_summary_timeout_seconds),
                    retry_count=int(self.summary_config.compact_summary_retry_count),
                    initial_retry_delay_seconds=int(self.summary_config.compact_summary_initial_retry_delay_seconds),
                    retry_multiplier=int(self.summary_config.compact_summary_retry_multiplier),
                )
            if summary is None:
                summary = build_deterministic_summary(old_summary, evicted, task_goal=memory_candidate.task_goal)
            artifact_chain = []
            if old_summary_event and isinstance(old_summary_event.metadata, dict):
                artifact_chain = [dict(ref) for ref in old_summary_event.metadata.get("artifact_chain", []) if isinstance(ref, dict)]
            if not any(ref.get("path") == history_artifact_ref["path"] for ref in artifact_chain):
                artifact_chain.append(history_artifact_ref)
            summary.artifact_paths = [str(ref.get("path")) for ref in artifact_chain if ref.get("path")]
            summary_turn_id = self._compact_summary_turn_id(ids)
            summary_event = HistoryEvent("compact_summary", f"compact-{session_candidate.get('event_seq', 0) + 1}", summary_turn_id, summary.model_dump_json(exclude_none=True), metadata={"summary_version": summary.summary_version, "history_artifact": history_artifact_ref, "artifact_chain": artifact_chain})
            history = [summary_event] + [event for event in verified_history if event.turn_id in keep_ids and event.kind != "compact_summary"]
            session_candidate["history"] = [event.to_dict() for event in history]
            session_candidate["event_seq"] = int(session_candidate.get("event_seq", 0)) + 1
            session_commit_required = True
            summary_model_succeeded = bool(summary_model_audit and summary_model_audit.get("status") == "success")
            compact_audit = {"mode": "model" if summary_model_succeeded else "deterministic", "source": "summary_model" if summary_model_succeeded else "rule", "status": "applied" if summary_model_succeeded else "fallback", "fallback_reason": (summary_model_audit or {}).get("fallback_reason", ""), "summary_text": summary.model_dump_json(exclude_none=True), "artifact_ref": history_artifact_ref, "summary_model": summary_model_audit or {}}
        history = self._build_structured_history(session_candidate, pressure_level=level)[0]
        skill = render_skill_section(select_skill_entries(level))
        if level >= 3:
            self._reduce_memory(memory_candidate)
        result = ContextResult(prefix, skill, history, memory_candidate, tools, {}, provider_continuation=continuation, internal_continuation_instruction=internal_instruction)
        snapshot = compile_provider_input_snapshot(result, self.tokenizer)
        result.provider_input = snapshot
        before_items = {str(item.name): int(item.tokens) for item in full_snapshot.occupancies}
        after_items = {str(item.name): int(item.tokens) for item in snapshot.occupancies}
        before_items.update({"output_reservation": self.actual_max_new_tokens, "safety_margin": SAFETY_MARGIN})
        after_items.update({"output_reservation": self.actual_max_new_tokens, "safety_margin": SAFETY_MARGIN})
        kept_ids = {event.event_id for event in history if event.event_id}
        changes = []
        for item in [HistoryEvent.from_dict(item) for item in original_history_items]:
            if item.event_id and item.event_id not in kept_ids:
                metadata = item.metadata if isinstance(item.metadata, dict) else {}
                changes.append({
                    "change": "compressed" if item.kind == "compact_summary" else "removed",
                    "category": item.kind,
                    "turn": item.turn_id,
                    "tool_name": str(metadata.get("tool_name") or metadata.get("name") or ""),
                    "summary": item.content,
                    "file_ref": str(metadata.get("artifact_ref") or ""),
                })
        before_pressure = dict(pressure)
        after_pressure = calculate_pressure(0, max(1, window - snapshot.serialized_input_tokens - self.actual_max_new_tokens - SAFETY_MARGIN))
        compression_comparison = {
            "before": {"fixed_items": before_items, "total_input_tokens": full_snapshot.serialized_input_tokens, "output_reserved_tokens": self.actual_max_new_tokens, "safety_margin_tokens": SAFETY_MARGIN, "remaining_capacity_tokens": max(0, window - full_snapshot.serialized_input_tokens - self.actual_max_new_tokens - SAFETY_MARGIN), "pressure_ratio": before_pressure.get("ratio", 0), "pressure_level": level},
            "after": {"fixed_items": after_items, "total_input_tokens": snapshot.serialized_input_tokens, "output_reserved_tokens": self.actual_max_new_tokens, "safety_margin_tokens": SAFETY_MARGIN, "remaining_capacity_tokens": window - snapshot.serialized_input_tokens - self.actual_max_new_tokens - SAFETY_MARGIN, "pressure_ratio": after_pressure.get("ratio", 0), "pressure_level": self._pressure_level(after_pressure.get("ratio", 0))[0]},
            "delta": {"fixed_items": {name: after_items.get(name, 0) - before_items.get(name, 0) for name in sorted(set(before_items) | set(after_items))}, "total_released_tokens": max(0, full_snapshot.serialized_input_tokens - snapshot.serialized_input_tokens)},
            "content_changes": changes,
        }
        capacity = validate_final_capacity(self.model_profile, self.actual_max_new_tokens, snapshot.serialized_input_tokens)
        if not capacity.can_send:
            raise FinalContextExceedsWindowError(audit={"effective_context_window_tokens": capacity.effective_context_window_tokens, "serialized_input_tokens": capacity.serialized_input_tokens, "actual_max_new_tokens": capacity.actual_max_new_tokens, "compression_comparison": compression_comparison, "compression_status": "compressed_but_still_exceeds_window" if level == 4 else "pressure_governance_insufficient"})
        section_demands = {
            "skills": self.tokenizer.count(skill),
            "history": self.tokenizer.count(json.dumps([event.to_dict() for event in history], ensure_ascii=False)),
            "working_memory": self.tokenizer.count(memory_candidate.render()),
        }
        pressure_level, pressure_range, _ = self._pressure_level(pressure.get("ratio", 0))
        compact_status = "applied" if compact_audit else "idle"
        result.ctx_info = {
            "model_profile": getattr(self.model_profile, "snapshot", lambda: {})(),
            "effective_context_window_tokens": capacity.effective_context_window_tokens,
            "actual_max_new_tokens": capacity.actual_max_new_tokens,
            "safety_margin_tokens": SAFETY_MARGIN,
            "serialized_input_tokens": snapshot.serialized_input_tokens,
            "fixed_occupancies": [{"name": item.name, "tokens": item.tokens, "source": item.source} for item in snapshot.occupancies] + [{"name": "output_reservation", "tokens": self.actual_max_new_tokens, "source": "output_reservation"}, {"name": "safety_margin", "tokens": SAFETY_MARGIN, "source": "safety_margin"}],
            "raw_section_demands": section_demands,
            "actual_section_minimums": {name: min(value, minimum) for name, value, minimum in (("history", section_demands["history"], 8192), ("working_memory", section_demands["working_memory"], 4096), ("skills", section_demands["skills"], 512))},
            "level_required_tokens": dict(section_demands),
            "selected_section_tokens": dict(section_demands),
            "raw_pressure": dict(pressure),
            "final_pressure": calculate_pressure(0, max(1, capacity.remaining_tokens)),
            "compression_records": [],
            "pressure": {**pressure, "level": pressure_level, "range": pressure_range, "final_ratio": calculate_pressure(0, max(1, capacity.remaining_tokens)).get("ratio", 0)},
            "final_capacity_status": {"can_send": capacity.can_send, "remaining_tokens": capacity.remaining_tokens},
            "tokenizer_source": snapshot.tokenizer_source,
            "compact": {"status": compact_status, "should_compact": level == 4, "trigger": "semantic_summary" if level == 4 else ""},
        }
        # 保存前后上下文对比，供运行时事件和 Web 步骤卡直接展示。
        if level >= 1:
            result.ctx_info["compression_comparison"] = compression_comparison
        result.compact_audit = compact_audit
        return ContextBuildOutcome(result, session_candidate, memory_candidate, session_commit_required, history_artifact_ref)

    @staticmethod
    def _compact_summary_turn_id(turn_ids: list[str]) -> str:
        """为压缩摘要选择稳定锚点，历史不足三回合时使用最早回合。"""
        if not turn_ids:
            return ""
        return turn_ids[-3] if len(turn_ids) >= 3 else turn_ids[0]

    @staticmethod
    def _select_history(history: list[HistoryEvent], level: int) -> list[HistoryEvent]:
        if level == 0:
            return history
        windows = {1: 5, 2: 4, 3: 3, 4: 3}
        ids = []
        for event in history:
            if event.turn_id not in ids:
                ids.append(event.turn_id)
        keep = set(ids[-windows.get(level, 3):])
        selected = [event for event in history if event.turn_id in keep or event.kind == "compact_summary"]
        current_turn = ids[-1] if ids else ""
        selected = ContextManager._remove_orphan_tool_events(selected, {current_turn})
        return selected

    @staticmethod
    def _remove_orphan_tool_events(history: list[HistoryEvent], current_turns: set[str]) -> list[HistoryEvent]:
        """闭合旧回合只保留成对的 tool call/result，当前回合允许暂存未闭环调用。"""
        calls = {str(event.call_id) for event in history if event.kind == "tool_call" and event.call_id}
        results = {str(event.call_id) for event in history if event.kind == "tool_result" and event.call_id}
        closed = calls & results
        return [
            event
            for event in history
            if event.kind not in {"tool_call", "tool_result"}
            or event.turn_id in current_turns
            or (event.call_id and str(event.call_id) in closed)
        ]

    @staticmethod
    def _history_window_for_level(level: int) -> int:
        """返回包含当前回合的原始 History 窗口大小。"""
        return {0: 7, 1: 5, 2: 4, 3: 3, 4: 3}.get(int(level), 3)

    def _build_structured_history(self, session: dict, *, pressure_level: int, requested_message: str = "") -> tuple[list[HistoryEvent], list[dict]]:
        history = [HistoryEvent.from_dict(item) for item in session.get("history", [])]
        selected = self._select_history(history, pressure_level)
        if pressure_level == 0:
            return selected, []
        # 窗口外 list_files 按规范化 path 仅保留最后一组完整调用和结果。
        recent_ids = {item.event_id for item in selected}
        latest_by_path: dict[str, tuple[HistoryEvent, HistoryEvent | None]] = {}
        for index, event in enumerate(history):
            if event.kind != "tool_call" or event.tool_name != "list_files":
                continue
            path = self._normalize_list_files_path(str((event.arguments or {}).get("path", ".")))
            result = next((candidate for candidate in history[index + 1:] if candidate.kind == "tool_result" and candidate.call_id == event.call_id), None)
            latest_by_path[path] = (event, result)
        for call, result in latest_by_path.values():
            if call.event_id not in recent_ids and result is not None:
                selected.extend([call, result])
        order = {event.event_id: index for index, event in enumerate(history)}
        selected.sort(key=lambda event: order.get(event.event_id, len(order)))
        return selected, []

    def _normalize_list_files_path(self, value: str) -> str:
        """将 list_files 的路径归一为工作区相对 POSIX 路径。"""
        raw = str(value or ".").strip() or "."
        try:
            return self.workspace.relpath(self.workspace.resolve_path(raw)).replace("\\", "/") or "."
        except Exception:
            return raw.replace("\\", "/") or "."

    @staticmethod
    def _reduce_memory(memory) -> None:
        """高压时只保留当前任务所需的热文件和未完成状态。"""
        memory.recent_files = memory.recent_files[-12:]
        memory.retrieved_memory = []
        memory.subagent_results = []

    def _reduce_working_memory_for_pressure(self, memory) -> None:
        """公开压力治理入口，供运行时和审计测试复用。"""
        self._reduce_memory(memory)

    @staticmethod
    def _pressure_level(ratio: float) -> tuple[int, str, str]:
        """将审计比例映射到 9.5 五档压力。"""
        value = float(ratio)
        if value < 0.60:
            return 0, "0-60", "tier0"
        if value < 0.75:
            return 1, "60-75", "tier1"
        if value < 0.85:
            return 2, "75-85", "tier2"
        if value < 0.95:
            return 3, "85-95", "tier3"
        return 4, "95+", "tier4"
