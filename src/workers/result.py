from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class WorkerResult:
    worker_id: str  # 子 Agent ID
    role: str  # 子 Agent 角色
    status: str  # 终态状态
    text: str  # 子 Agent 最终摘要
    changed_files: list[str]  # 工具或 shell 检测到的变更路径
    artifacts: list[str]  # 结果和完整输出 artifact
    verification: dict[str, Any]  # 测试、编译或构建验证
    tool_failures: list[dict[str, Any]]  # 未解决工具失败
    stop_reason: str  # 停止原因
    steps: int  # 已执行模型步骤
    model_profile: dict[str, Any]  # 本次冻结的模型档案
