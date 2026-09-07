"""JCode 9.5 token 预算、压力和容量校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import ceil
from typing import Callable, Iterable

# 统一有效窗口上限；实际值仍受模型档案声明的窗口限制。
MAX_CONTEXT_TOKENS = 375000
DEFAULT_MAX_NEW_TOKENS = 16384
SAFETY_MARGIN = 7500


def estimate_tokens(text: str) -> int:
    """使用统一回退规则估算 token，供独立工具测试使用。"""
    return TokenizerAdapter().count(str(text))


class TokenizerAdapter:
    """统一封装 Provider tokenizer，缺失时使用可审计的字符回退。"""
    def __init__(self, counter: Callable[[str], int] | None = None, source: str = "fallback_chars_div_4"):
        self.counter = counter
        self.source = source if counter else "fallback_chars_div_4"

    def count(self, text: str) -> int:
        if self.counter is not None:
            return max(0, int(self.counter(text)))
        return ceil(len(text) / 4) if text else 0


@dataclass(frozen=True)
class BudgetOccupancy:
    name: str  # 占用名称
    tokens: int  # 占用 token 数
    source: str  # input、output_reservation 或 safety_margin


class ReasoningContinuationBudgetOccupant:
    """计算 reasoning 原生续接项占用。"""
    def __init__(self, items: Iterable[dict] | None = None, tokenizer: TokenizerAdapter | None = None):
        self.items = list(items or [])
        self.tokenizer = tokenizer or TokenizerAdapter()
        self.tokens = self.measure(self.items, self.tokenizer).tokens

    def measure(self, items: Iterable[dict], tokenizer: TokenizerAdapter) -> BudgetOccupancy:
        selected = [item for item in items if isinstance(item, dict) and item.get("type") == "reasoning"]
        return BudgetOccupancy("reasoning_continuation", tokenizer.count(_compact_json(selected)) if selected else 0, "input")


class ToolContinuationBudgetOccupant:
    """计算 function_call/function_call_output 原生续接项占用。"""
    def __init__(self, items: Iterable[dict] | None = None, tokenizer: TokenizerAdapter | None = None):
        self.items = list(items or [])
        self.tokenizer = tokenizer or TokenizerAdapter()
        self.tokens = self.measure(self.items, self.tokenizer).tokens

    def measure(self, items: Iterable[dict], tokenizer: TokenizerAdapter) -> BudgetOccupancy:
        selected = [item for item in items if isinstance(item, dict) and item.get("type") in {"function_call", "function_call_output"}]
        return BudgetOccupancy("tool_continuation", tokenizer.count(_compact_json(selected)) if selected else 0, "input")


@dataclass(frozen=True)
class ContextCapacity:
    effective_context_window_tokens: int  # 本轮有效窗口
    actual_max_new_tokens: int  # 输出预留
    safety_margin_tokens: int  # 固定安全余量
    serialized_input_tokens: int  # instructions/input/tools 输入 token
    remaining_tokens: int  # 可变内容剩余容量
    can_send: bool  # 是否满足最终容量公式


def effective_window(profile_window: int) -> int:
    return min(MAX_CONTEXT_TOKENS, int(profile_window))


def validate_static_model_capacity(profile, actual_max_new_tokens: int, static_input_tokens: int) -> None:
    """校验启动或模型切换时已知的静态容量。"""
    actual = int(actual_max_new_tokens)
    if actual <= 0 or actual > int(profile.max_output_tokens):
        raise ValueError("actual_max_new_tokens exceeds model output limit")
    if int(static_input_tokens) + actual + SAFETY_MARGIN > effective_window(profile.context_window_tokens):
        raise ValueError("static input exceeds effective model context window")


def validate_final_capacity(profile, actual_max_new_tokens: int, serialized_input_tokens: int) -> ContextCapacity:
    window = effective_window(profile.context_window_tokens)
    actual = int(actual_max_new_tokens)
    serialized = int(serialized_input_tokens)
    remaining = window - serialized - actual - SAFETY_MARGIN
    return ContextCapacity(window, actual, SAFETY_MARGIN, serialized, remaining, remaining >= 0)


def calculate_pressure(raw_flexible_demand: int, flexible_budget: int) -> dict:
    """使用整数交叉乘法确定 0-4 压力等级。"""
    raw = max(0, int(raw_flexible_demand))
    budget = max(0, int(flexible_budget))
    if budget == 0:
        level, ratio = (0, 0.0) if raw == 0 else (4, float("inf"))
    elif raw * 100 < budget * 60:
        level, ratio = 0, raw / budget
    elif raw * 100 < budget * 75:
        level, ratio = 1, raw / budget
    elif raw * 100 < budget * 85:
        level, ratio = 2, raw / budget
    elif raw * 100 < budget * 95:
        level, ratio = 3, raw / budget
    else:
        level, ratio = 4, raw / budget
    return {"level": level, "ratio": ratio, "raw_flexible_demand": raw, "flexible_budget": budget}


@dataclass(frozen=True)
class ContextBudgetCandidate:
    section: str  # 候选所属 section
    stable_name: str  # 稳定排序名称
    payload: object  # 不可拆原子内容
    source_order: int  # 原始顺序
    priority_class: int = 4  # 固定优先级类别
    user_mentioned: bool = False  # 是否用户点名
    active_or_unresolved: bool = False  # 是否活跃或未解决
    modified_in_current_run: bool = False  # 当前回合是否修改
    current_freshness: bool = False  # 是否当前 freshness
    incomplete_read: bool = False  # 是否未完成读取
    task_relevance: int = 0  # 任务相关性
    last_access_order: int = 0  # 最近访问顺序
    token_increment: int = 0  # 按当前 Provider 输入重建得到的增量

    def sort_key(self) -> tuple:
        return (self.priority_class, not self.user_mentioned, not self.active_or_unresolved, not self.modified_in_current_run, not self.current_freshness, not self.incomplete_read, -self.task_relevance, -self.last_access_order, self.source_order, self.stable_name)


def pack_budget_candidates(candidates: Iterable[ContextBudgetCandidate], budget: int, required_names: Iterable[str] = ()) -> tuple[list[ContextBudgetCandidate], list[dict]]:
    """按稳定键打包完整候选；必保原子放不下时返回明确溢出审计。"""
    from src.runtime.errors import SystemMinimumBudgetOverflowError

    remaining = max(0, int(budget))
    values = list(candidates)
    required = {str(name) for name in required_names}
    selected: list[ContextBudgetCandidate] = []
    decisions: list[dict] = []
    for candidate in sorted(values, key=lambda item: item.sort_key()):
        if candidate.stable_name in required:
            if candidate.token_increment > remaining:
                raise SystemMinimumBudgetOverflowError(audit={"candidate": candidate.stable_name, "required_tokens": candidate.token_increment, "remaining_tokens": remaining})
            selected.append(candidate)
            remaining -= candidate.token_increment
            decisions.append({"action": "required", "candidate": candidate.stable_name, "tokens": candidate.token_increment})
    for candidate in sorted(values, key=lambda item: item.sort_key()):
        if candidate.stable_name in required:
            continue
        accepted = candidate.token_increment <= remaining
        decisions.append({"action": "accept" if accepted else "reject", "candidate": candidate.stable_name, "tokens": candidate.token_increment, "remaining_tokens": remaining})
        if accepted:
            selected.append(candidate)
            remaining -= candidate.token_increment
    return selected, decisions


def serialize_counted_input(instructions: str, input_items: list[dict], tools: list[dict], tokenizer: TokenizerAdapter) -> tuple[str, list[BudgetOccupancy]]:
    """生成唯一规范 JSON，并按累计前缀计量真实 section 增量。

    用累计前缀而不是分别估算字段，避免 tokenizer 在 JSON 边界处合并 token。
    """
    segments = [
        ("provider_protocol_envelope", '{"instructions":'),
        ("instructions", json.dumps(instructions, ensure_ascii=False, separators=(",", ":"))),
        ("provider_protocol_envelope", ',"input":'),
        ("history", json.dumps(input_items, ensure_ascii=False, separators=(",", ":"))),
        ("provider_protocol_envelope", ',"tools":'),
        ("tools_schema", json.dumps(tools, ensure_ascii=False, separators=(",", ":"))),
        ("provider_protocol_envelope", "}"),
    ]
    serialized = ""
    totals: dict[str, int] = {}
    previous = 0
    for name, fragment in segments:
        serialized += fragment
        current = tokenizer.count(serialized)
        totals[name] = totals.get(name, 0) + max(0, current - previous)
        previous = current
    final_tokens = tokenizer.count(serialized)
    accounted = sum(totals.values())
    if accounted != final_tokens:
        totals["provider_protocol_envelope"] = totals.get("provider_protocol_envelope", 0) + final_tokens - accounted
    return serialized, [
        BudgetOccupancy(name, tokens, "input")
        for name, tokens in totals.items()
        if tokens or name in {"instructions", "history", "working_memory", "skills", "tools_schema", "provider_protocol_envelope"}
    ]


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
