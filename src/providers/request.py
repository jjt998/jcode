"""Responses API 输入编译器，Provider 与审计共用同一份结果。"""

from __future__ import annotations

import json

from src.context.budget import BudgetOccupancy, ReasoningContinuationBudgetOccupant, TokenizerAdapter, ToolContinuationBudgetOccupant, serialize_counted_input
from src.context.result import ContextResult, HistoryEvent, ProviderInputSnapshot, ToolDefinition
from src.runtime.errors import UnsupportedProviderContinuationItemError


def compile_responses_input(context: ContextResult) -> list[dict]:
    items: list[dict] = []
    if context.skill.strip():
        items.append({"role": "user", "content": context.skill})
    continuation = context.provider_continuation or {}
    continuation_items = [item for item in continuation.get("items", []) if isinstance(item, dict)]
    for item in continuation_items:
        if item.get("type") not in {"reasoning", "function_call", "function_call_output"}:
            raise UnsupportedProviderContinuationItemError(f"unsupported continuation item: {item.get('type')}")
    continuation_ids = {str(item.get("call_id")) for item in continuation_items if item.get("call_id")}
    for event in context.history:
        if event.kind in {"tool_call", "tool_result"} and str(event.call_id or "") in continuation_ids:
            continue
        items.extend(_compile_history_event(event))
    items.extend(dict(item) for item in continuation_items)
    memory_text = context.working_memory.render().strip()
    if memory_text:
        items.append({"role": "user", "content": memory_text})
    if context.internal_continuation_instruction.strip():
        items.append({"role": "user", "content": context.internal_continuation_instruction})
    return items


def _compile_history_event(event: HistoryEvent) -> list[dict]:
    if event.kind == "compact_summary":
        return [{"role": "user", "content": "[JCode Compact Summary]\n" + event.content}]
    if event.kind == "user":
        return [{"role": "user", "content": event.content}]
    if event.kind == "assistant":
        return [{"role": "assistant", "content": event.content}]
    if event.kind == "tool_call":
        return [{"type": "function_call", "call_id": event.call_id, "name": event.tool_name, "arguments": json.dumps(event.arguments or {}, ensure_ascii=False, separators=(",", ":"))}]
    if event.kind == "tool_result":
        return [{"type": "function_call_output", "call_id": event.call_id, "output": event.content}]
    return []


def compile_responses_tools(tools: list[ToolDefinition]) -> list[dict]:
    return [{"type": "function", "name": tool.name, "description": tool.description, "parameters": tool.parameters} for tool in tools]


def compile_provider_input_snapshot(context: ContextResult, tokenizer: TokenizerAdapter | None = None) -> ProviderInputSnapshot:
    adapter = tokenizer or TokenizerAdapter()
    continuation_items = [item for item in (context.provider_continuation or {}).get("items", []) if isinstance(item, dict)]
    input_items = compile_responses_input(context)
    tool_items = compile_responses_tools(context.tools)
    serialized, occupancies = serialize_counted_input(context.prefix, input_items, tool_items, adapter)
    reasoning = ReasoningContinuationBudgetOccupant(continuation_items, adapter).tokens
    tool = ToolContinuationBudgetOccupant(continuation_items, adapter).tokens
    totals = {item.name: item.tokens for item in occupancies}
    history_tokens = totals.get("history", 0)
    reasoning_in_history = min(reasoning, history_tokens)
    tool_in_history = min(tool, max(0, history_tokens - reasoning_in_history))
    totals["history"] = history_tokens - reasoning_in_history - tool_in_history
    totals["reasoning_continuation"] = reasoning_in_history
    totals["tool_continuation"] = tool_in_history
    named = [BudgetOccupancy(name, tokens, "input") for name, tokens in totals.items()]
    return ProviderInputSnapshot(
        instructions=context.prefix,
        input=input_items,
        tools=tool_items,
        serialized_input_json=serialized,
        serialized_input_tokens=adapter.count(serialized),
        tokenizer_source=adapter.source,
        occupancies=tuple(named),
    )
