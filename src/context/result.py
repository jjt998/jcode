from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


HistoryKind = Literal["user", "assistant", "tool_call", "tool_result", "compact_summary"]


@dataclass
class HistoryEvent:
    kind: HistoryKind  # 历史事件类别
    event_id: str  # 会话内唯一事件标识
    turn_id: str  # 所属运行回合标识
    content: str = ""  # 用户、助手、工具结果或摘要正文
    tool_name: str | None = None  # 工具名称
    call_id: str | None = None  # Provider 工具调用关联标识
    arguments: dict | None = None  # 工具调用参数
    metadata: dict = field(default_factory=dict)  # artifact、stale 与 Provider 原生回放数据

    @classmethod
    def from_dict(cls, item: dict) -> "HistoryEvent":
        """将 session 事件转换为 Context 层的受信任结构。"""
        role = str(item.get("role") or "")
        kind = str(item.get("kind") or "")
        if kind not in {"user", "assistant", "tool_call", "tool_result", "compact_summary"}:
            kind = {"user": "user", "assistant": "assistant", "tool": "tool_result"}.get(role, "assistant")
        return cls(
            kind=kind,  # type: ignore[arg-type]
            event_id=str(item.get("event_id") or ""),
            turn_id=str(item.get("turn_id") or item.get("run_id") or ""),
            content=str(item.get("content") or ""),
            tool_name=str(item.get("tool_name") or item.get("name") or "") or None,
            call_id=str(item.get("call_id") or "") or None,
            arguments=dict(item.get("arguments") or item.get("args") or {}) or None,
            metadata=dict(item.get("metadata") or {}),
        )

    def to_dict(self) -> dict:
        """生成可持久化且与 Provider 无关的 session 事件。"""
        return {
            "kind": self.kind,
            "event_id": self.event_id,
            "turn_id": self.turn_id,
            "content": self.content,
            "tool_name": self.tool_name,
            "call_id": self.call_id,
            "arguments": self.arguments,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ToolDefinition:
    name: str  # 原生函数工具名称
    description: str  # 工具能力说明
    parameters: dict  # JSON Schema 参数定义
    read_only: bool  # 是否只读
    risky: bool  # 是否有写入或执行风险


@dataclass
class ContextResult:
    prefix: str  # 稳定系统规则与项目规则
    skill: str  # 本轮技能上下文
    history: list[HistoryEvent]  # 经治理后的会话历史
    working_memory: object  # 构建时冻结的短期记忆快照
    current_request: str  # 当前真实用户请求
    tools: list[ToolDefinition]  # 当前允许调用的原生工具
    ctx_info: dict  # 压力、裁剪与压缩审计
    compact_audit: dict | None = None  # 历史语义压缩审计

