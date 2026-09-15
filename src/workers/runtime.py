from __future__ import annotations

from typing import Any

from src.workers.result import WorkerResult


class WorkerRuntime:
    worker_id: str  # 子 Agent ID
    role: str  # 固定角色 ID
    prompt: str  # 子任务目标
    acceptance_criteria: list[str]  # 可检查完成条件
    write_scope: list[str]  # 允许写入的路径范围
    model_profile: dict[str, Any]  # 冻结模型档案
    parent_session_id: str  # 父会话 ID
    parent_run_id: str  # 父运行 ID
    messages: list[str]  # 父 Agent 补充消息
    status: str  # 当前生命周期状态
    result: WorkerResult | None  # 已持久化的终态结果

    def __init__(
        self,
        worker_id: str,
        prompt: str,
        *,
        role: str,
        acceptance_criteria: list[str],
        write_scope: list[str] | None = None,
        model_profile: dict | None = None,
        parent_session_id: str = "",
        parent_run_id: str = "",
    ):
        self.worker_id = worker_id
        self.role = role
        self.prompt = prompt
        self.acceptance_criteria = list(acceptance_criteria)
        self.write_scope = list(write_scope or [])
        self.model_profile = dict(model_profile or {})
        self.parent_session_id = parent_session_id
        self.parent_run_id = parent_run_id
        self.messages: list[str] = []
        self.status = "created"
        self.result = None

    def send(self, message: str) -> None:
        """只允许在任务开始前补充消息，避免运行中改变冻结输入。"""
        if self.status != "created":
            raise ValueError("subagent_not_messageable")
        self.messages.append(str(message))

    def to_dict(self) -> dict:
        """生成 worker 任务状态，供恢复审计和 Web 展示读取。"""
        return {
            "worker_id": self.worker_id,
            "role": self.role,
            "prompt": self.prompt,
            "acceptance_criteria": list(self.acceptance_criteria),
            "write_scope": list(self.write_scope),
            "model_profile": dict(self.model_profile),
            "parent_session_id": self.parent_session_id,
            "parent_run_id": self.parent_run_id,
            "messages": list(self.messages),
            "status": self.status,
            "result": self.result.__dict__ if self.result else None,
        }
