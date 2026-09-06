from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.context.budget import BudgetOccupancy
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
    tools: list[ToolDefinition]  # 当前允许调用的原生工具
    ctx_info: dict  # 压力、裁剪与压缩审计
    compact_audit: dict | None = None  # 历史语义压缩审计
    provider_continuation: dict = field(default_factory=dict)  # 同一 run 的 Provider 原生续接项
    internal_continuation_instruction: str = ""  # 输出截断后的内部续写指令
    provider_input: "ProviderInputSnapshot | None" = None  # 唯一 Provider 输入快照


@dataclass(frozen=True)
class ProviderInputSnapshot:
    """Provider 请求、预览和审计共同使用的输入快照。"""
    instructions: str  # Provider instructions
    input: list[dict]  # Responses 原生 input items
    tools: list[dict]  # Responses 工具 schema
    serialized_input_json: str  # 规范序列化文本
    serialized_input_tokens: int  # 规范输入 token 数
    tokenizer_source: str  # tokenizer 来源
    occupancies: tuple[BudgetOccupancy, ...] = ()  # 输入占用明细


@dataclass(frozen=True)
class ContextBuildOutcome:
    """上下文结果与待提交 session/Working Memory 候选。"""
    context_result: ContextResult  # 构建结果
    session_candidate: dict | None = None  # 待提交 session
    working_memory_candidate: Any | None = None  # 待提交 Working Memory
    session_commit_required: bool = False  # 是否需要原子提交
    history_artifact_ref: dict | None = None  # 已验签历史 artifact

    def __getattr__(self, name: str):
        """兼容调用方读取上下文字段，真实数据仍归属于 context_result。"""
        return getattr(self.context_result, name)
