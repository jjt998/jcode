from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class EvaluationSystem:
    """描述可在同一 Case 上运行的被测系统。"""

    system_id: str  # 对照系统标识
    description: str  # 对照系统说明
    command_builder: Callable[[str, str], list[str]]  # workspace、prompt 到命令


def available_systems() -> tuple[EvaluationSystem, ...]:
    """返回当前已注册的基线名称，具体运行由 runner 负责。"""
    return (
        EvaluationSystem("baseline-direct", "直接模型调用", lambda workspace, prompt: [prompt]),
        EvaluationSystem("baseline-react", "简单 ReAct 工具循环", lambda workspace, prompt: [prompt]),
        EvaluationSystem("jcode", "完整 JCode Harness", lambda workspace, prompt: [prompt]),
    )
