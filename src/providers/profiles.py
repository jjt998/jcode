from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ReasoningMode = Literal["none", "native", "optional"]


@dataclass(frozen=True)
class ModelProfile:
    """描述一个可被 session 选择的模型档案。"""

    id: str
    provider: str
    api_protocol: str  # Provider 固定的原生 API 协议
    model: str
    api_key: str
    base_url: str
    context_window_tokens: int  # 模型上下文窗口上限
    max_output_tokens: int  # 模型单次最大输出上限
    reasoning_mode: ReasoningMode = "none"
    thinking_enabled: bool = False
    reasoning_effort: str = ""
    reasoning_effort_options: tuple[str, ...] = ()
    reasoning_always_on: bool = False  # 模型是否无法关闭推理
    extra: dict[str, object] = field(default_factory=dict)

    def snapshot(self) -> dict:
        """生成可写入 run 证据的无密钥快照。"""
        return {
            "id": self.id,
            "provider": self.provider,
            "api_protocol": self.api_protocol,
            "model": self.model,
            "reasoning_mode": self.reasoning_mode,
            "thinking_enabled": self.thinking_enabled,
            "reasoning_effort": self.reasoning_effort,
            "reasoning_effort_options": list(self.reasoning_effort_options),
            "reasoning_always_on": self.reasoning_always_on,
            "context_window_tokens": self.context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
        }
