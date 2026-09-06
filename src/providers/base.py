from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.context.result import ContextResult


class ProviderRequestError(RuntimeError):
    """Provider 请求失败，携带运行时恢复策略所需的结构化信息。"""

    def __init__(self, message: str, *, status_code: int = 0, retry_after_seconds: float | None = None, transport_error: bool = False):
        super().__init__(message)
        self.status_code = int(status_code)  # HTTP 状态码，网络错误为 0
        self.retry_after_seconds = retry_after_seconds  # 服务端建议的重试等待秒数
        self.transport_error = bool(transport_error)  # 无 HTTP 状态的临时网络异常标记


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
    incomplete_reason: str = ""  # incomplete_details.reason 的原始值
    provider_error_code: str = ""  # Provider 响应错误码
    provider_error_message: str = ""  # Provider 响应错误摘要
    retry_after_seconds: float | None = None  # Provider 建议的等待秒数


class ModelClient(Protocol):
    model: str

    def complete(self, context: ContextResult, *, model: str, max_tokens: int, temperature: float, model_profile: dict | None = None) -> ModelResponse:
        ...

    def complete_summary(self, summary_provider_input: dict, *, profile_id: str, max_output_tokens: int, timeout_seconds: int = 120) -> str:
        ...
