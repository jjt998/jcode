from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json


def now_iso() -> str:
    """生成统一的 UTC 审计时间。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EvalCase:
    """描述一个可重复执行的评测场景。"""

    case_id: str  # Case 唯一标识
    eval_version: str  # 评测协议版本
    category: str  # 评测类别
    scenario: str  # 类别内场景编号
    description: str  # 场景说明
    prompt: str  # 发送给 JCode 的任务
    workspace_fixture: str | None = None  # 初始 workspace fixture
    model_profile: str | None = None  # 使用的模型档案
    allowed_tools: tuple[str, ...] = ()  # 允许工具集合
    write_scope: tuple[str, ...] = ()  # 允许写入范围
    fault_injection: dict[str, Any] = field(default_factory=dict)  # 故障注入参数
    acceptance_predicates: tuple[dict[str, Any], ...] = ()  # 自动验收谓词
    forbidden_behaviors: tuple[str, ...] = ()  # 禁止行为
    config: dict[str, Any] = field(default_factory=dict)  # 运行参数

    @classmethod
    def from_dict(cls, value: dict[str, Any], base_dir: Path | None = None) -> "EvalCase":
        """严格读取 Case 字段，缺失必需字段时直接报错。"""
        required = ("case_id", "eval_version", "category", "scenario", "description", "prompt")
        missing = [name for name in required if not str(value.get(name, "")).strip()]
        if missing:
            raise ValueError(f"case missing required fields: {', '.join(missing)}")
        fixture = value.get("workspace_fixture")
        if fixture and base_dir:
            fixture = str((base_dir / str(fixture)).resolve())
        return cls(
            case_id=str(value["case_id"]),
            eval_version=str(value["eval_version"]),
            category=str(value["category"]),
            scenario=str(value["scenario"]),
            description=str(value["description"]),
            prompt=str(value["prompt"]),
            workspace_fixture=fixture,
            model_profile=str(value["model_profile"]) if value.get("model_profile") else None,
            allowed_tools=tuple(str(item) for item in value.get("allowed_tools", [])),
            write_scope=tuple(str(item) for item in value.get("write_scope", [])),
            fault_injection=dict(value.get("fault_injection", {}) or {}),
            acceptance_predicates=tuple(dict(item) for item in value.get("acceptance_predicates", [])),
            forbidden_behaviors=tuple(str(item) for item in value.get("forbidden_behaviors", [])),
            config=dict(value.get("config", {}) or {}),
        )

    @classmethod
    def load(cls, path: str | Path) -> "EvalCase":
        path = Path(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("case document must be a JSON object")
        return cls.from_dict(value, path.parent)


@dataclass
class RunRecord:
    """保存一次评测运行的标准化事实。"""

    case_id: str  # 评测 Case
    eval_version: str  # Case 协议版本
    runner_version: str  # 运行器版本
    scorer_version: str  # 评分器版本
    jcode_revision: str  # JCode 代码版本
    run_id: str  # 本次运行标识
    started_at: str  # 开始时间
    finished_at: str  # 结束时间
    final_status: str  # 进程或 Agent 终态
    task_success: bool | None = None  # 任务是否满足验收谓词
    initial_workspace_hash: str = ""  # 初始 workspace 指纹
    final_workspace_hash: str = ""  # 最终 workspace 指纹
    changed_files: list[str] = field(default_factory=list)  # 变更文件
    tool_attempts: list[dict[str, Any]] = field(default_factory=list)  # 工具尝试
    verification: dict[str, Any] = field(default_factory=dict)  # 验证结果
    checkpoint_status: str = ""  # checkpoint 状态
    context_usage: dict[str, Any] = field(default_factory=dict)  # 上下文统计
    memory_observations: dict[str, Any] = field(default_factory=dict)  # 记忆统计
    agent_quality: dict[str, Any] = field(default_factory=dict)  # 任务执行质量
    harness_quality: dict[str, Any] = field(default_factory=dict)  # Harness 指令执行质量
    assurance: dict[str, Any] = field(default_factory=dict)  # 结果与证据可信度
    latency_ms: int = 0  # 总耗时
    failure_taxonomy: list[str] = field(default_factory=list)  # 失败分类
    evidence_paths: dict[str, str] = field(default_factory=dict)  # 证据路径
    process_exit_code: int | None = None  # JCode 进程退出码
    stdout: str = ""  # 标准输出摘要
    stderr: str = ""  # 标准错误摘要
    trace_events: list[dict[str, Any]] = field(default_factory=list)  # trace 事件快照
    report: dict[str, Any] = field(default_factory=dict)  # report 快照
    checkpoint: dict[str, Any] = field(default_factory=dict)  # checkpoint 快照

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化的 JSON 对象。"""
        return dict(self.__dict__)

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "RunRecord":
        """从持久化 JSON 恢复运行事实。"""
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("run record must be a JSON object")
        return cls(**value)
