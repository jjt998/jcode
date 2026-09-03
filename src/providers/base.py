from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.context.result import ContextResult


@dataclass
class ModelToolCall:
    call_id: str  # Provider 返回的调用标识
    name: str  # 工具名称
    arguments: dict  # 已解析的函数参数
    provider_metadata: dict  # 原生调用项，供历史回放使用


@dataclass
class ModelResponse:
    text: str  # 无工具调用时的最终回复内容
    reasoning: str = ""
    tool_calls: list[ModelToolCall] | None = None  # Provider 原生工具调用
    finish_reason: str = ""  # Provider 返回的结束原因
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict | None = None


class ModelClient(Protocol):
    model: str

    def complete(self, context: ContextResult, *, model: str, max_tokens: int, temperature: float, model_profile: dict | None = None) -> ModelResponse:
        ...
