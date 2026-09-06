from __future__ import annotations

import uuid
import hashlib
from dataclasses import dataclass, field

from src.state.workspace import now_iso


@dataclass
class TaskState:
    run_id: str
    task_id: str
    user_request: str
    model_profile: dict = field(default_factory=dict)
    step_index: int = 0
    attempts: int = 0
    tool_steps: int = 0
    status: str = "running"
    stop_reason: str = ""
    last_action: dict = field(default_factory=dict)
    completed_steps: list[str] = field(default_factory=list)
    pending_next_step: str = ""
    changed_files: list[str] = field(default_factory=list)
    failed_tools: list[dict] = field(default_factory=list)
    tool_attempts: list[dict] = field(default_factory=list)  # 本次运行全部工具尝试
    unresolved_tool_failures: list[dict] = field(default_factory=list)  # 当前仍未解决的工具失败
    resolved_tool_failures: list[dict] = field(default_factory=list)  # 已由后续证据解决的工具失败
    verification: dict = field(default_factory=dict)  # 最近验证命令及状态
    final_readiness_summary: dict = field(default_factory=dict)  # Final Gate 最近决策摘要
    provider_continuation: dict = field(default_factory=dict)  # 当前 run 的 Provider 原生续接项
    output_continuation_count: int = 0  # 模型输出被截断后的续写次数
    partial_response_parts: list[str] = field(default_factory=list)  # 截断响应中已保留的正文片段
    final_answer: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @classmethod
    def create(cls, user_request: str, model_profile: dict | None = None) -> "TaskState":
        return cls(run_id=f"run-{uuid.uuid4().hex[:10]}", task_id=f"task-{uuid.uuid4().hex[:10]}", user_request=user_request, model_profile=dict(model_profile or {}))

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    def record_tool(self, name: str, result, *, arguments: dict | None = None, call_id: str = "") -> None:
        self.tool_steps += 1
        if result.changed_files:
            known = set(self.changed_files)
            for path in result.changed_files:
                if path not in known:
                    self.changed_files.append(path)
                    known.add(path)
        path = str((arguments or {}).get("path") or (arguments or {}).get("file") or "").replace("\\", "/")
        attempt = {"name": name, "status": result.status, "error_type": result.error_type, "path": path, "call_id": call_id, "step_index": self.step_index}
        self.tool_attempts.append(attempt)
        if result.status not in {"success", "ok"}:
            failure = {
                "failure_id": f"failure-{len(self.tool_attempts)}-{hashlib.sha1((name + call_id + path).encode('utf-8')).hexdigest()[:8]}",
                "call_id": call_id,
                "tool_name": name,
                "error_type": result.error_type or "tool_failed",
                "path": path,
                "status": "unresolved",
                "created_step": self.step_index,
                "resolved_by": "",
                "resolution_evidence": "",
            }
            self.failed_tools.append({"name": name, "status": result.status, "error_type": result.error_type, "path": path, "failure_id": failure["failure_id"]})
            self.unresolved_tool_failures.append(failure)
        elif result.status in {"success", "ok"} and name == "write_file" and path:
            # 同一路径的成功整文件写入可关闭 patch 匹配失败，但不关闭验证或权限失败。
            remaining = []
            for failure in self.unresolved_tool_failures:
                if failure.get("tool_name") == "apply_patch" and failure.get("path") == path and failure.get("error_type") in {"patch_nonunique", "patch_mismatch"}:
                    resolved = dict(failure, status="resolved", resolved_by="write_file", resolution_evidence=f"write_file:{call_id or 'success'}")
                    self.resolved_tool_failures.append(resolved)
                else:
                    remaining.append(failure)
            self.unresolved_tool_failures = remaining
        if name == "run_shell":
            self.record_verification(arguments or {}, result)
        self.updated_at = now_iso()

    def record_verification(self, arguments: dict, result) -> None:
        """记录可识别的测试、编译、lint、类型检查或构建命令。"""
        command = str(arguments.get("command") or "")
        lowered = command.lower()
        command_class = "unknown"
        for key, words in (("test", ("pytest", "unittest", "npm test", "cargo test")), ("compile", ("compileall", " py_compile", " tsc")), ("lint", ("ruff", "flake8", "eslint")), ("typecheck", ("mypy", "pyright", "typecheck")), ("build", (" build", "npm run build", "cargo build"))):
            if any(word in lowered for word in words):
                command_class = key
                break
        if command_class != "unknown":
            self.verification = {"state": "passed" if result.status in {"success", "ok"} else "failed", "command_class": command_class, "command": command, "step_index": self.step_index, "changed_paths": list(self.changed_files)}

    def record_gate_decision(self, decision: dict) -> None:
        """保存最近一次 Final Gate 决策，供 checkpoint 和 report 使用。"""
        self.final_readiness_summary = dict(decision)

    def finish(self, status: str, stop_reason: str, final_answer: str = "") -> None:
        self.status = status
        self.stop_reason = stop_reason
        self.final_answer = final_answer
        self.updated_at = now_iso()
