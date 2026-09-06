from __future__ import annotations

from dataclasses import dataclass
import re

from src.runtime.plan import plan_artifact_ready, runtime_mode_name, runtime_mode_plan_path


@dataclass(frozen=True)
class FinalReadiness:
    unresolved_failures: tuple[dict, ...]  # 当前未解决工具失败
    verification: dict  # 最近验证命令及其状态
    changed_paths: tuple[str, ...]  # 当前运行已变更路径
    required_artifacts: tuple[dict, ...]  # 用户要求的必需产物状态
    pending_todos: tuple[str, ...]  # 未完成 todo 标识
    governance_denials: tuple[dict, ...]  # 仍影响完成的治理拒绝
    context: dict  # 上下文压力、压缩和最终容量状态
    reasons: tuple[dict, ...]  # 本次评估生成的结构化原因


def build_final_readiness(task_state, session: dict | None = None, workspace=None, *, context: dict | None = None) -> FinalReadiness:
    unresolved = tuple(dict(item) for item in getattr(task_state, "unresolved_tool_failures", []) if item.get("status") == "unresolved")
    pending = tuple(_pending_todos(session))
    ctx = dict(context or {})
    required_artifacts = tuple(_required_artifacts(task_state.user_request, workspace))
    reasons: list[dict] = []
    if unresolved:
        reasons.append(_reason("unresolved_tool_failure", "hard", "存在尚未解决的工具失败。", {"failure_ids": [item.get("failure_id") for item in unresolved]}))
    verification = dict(getattr(task_state, "verification", {}) or {})
    if verification.get("state") == "failed":
        reasons.append(_reason("failed_verification", "hard", "最近一次验证命令失败。", {"verification": verification}))
    elif task_state.changed_files and not verification:
        reasons.append(_reason("changed_paths_without_verification", "soft", "文件已变更但尚未记录验证命令。", {"changed_paths": list(task_state.changed_files)}))
    if pending:
        reasons.append(_reason("unresolved_todo", "soft", "仍存在未完成 todo。", {"todo_ids": list(pending)}))
    missing_artifacts = [item for item in required_artifacts if not item["exists"]]
    if missing_artifacts:
        reasons.append(_reason("missing_required_artifact", "hard", "用户明确要求的输出文件尚未落盘。", {"paths": [item["path"] for item in missing_artifacts]}))
    if session is not None and workspace is not None and runtime_mode_name(session) == "plan" and not plan_artifact_ready(session, workspace, runtime_mode_plan_path(session)):
        reasons.append(_reason("plan_artifact_not_ready", "hard", "plan artifact 尚未准备完成。", {"path": runtime_mode_plan_path(session)}))
    if ctx.get("final_capacity_status", {}).get("can_send") is False:
        reasons.append(_reason("final_context_exceeds_window", "hard", "最终上下文超出有效窗口。", ctx.get("final_capacity_status", {})))
    pressure = dict(ctx.get("pressure", {}) or {})
    compact = dict(ctx.get("compact", {}) or {})
    if int(pressure.get("level", 0) or 0) >= 4 and compact.get("status") not in {"applied", "idle"}:
        reasons.append(_reason("context_pressure_without_compaction", "soft", "上下文压力达到 Level 4 但未完成压缩。", {"pressure": pressure, "compact": compact}))
    return FinalReadiness(unresolved, verification, tuple(task_state.changed_files), required_artifacts, pending, (), ctx, tuple(reasons))


def _pending_todos(session: dict | None) -> list[str]:
    if not isinstance(session, dict):
        return []
    ledger = session.get("todo_ledger", {})
    items = ledger.get("items", []) if isinstance(ledger, dict) else []
    return [str(item.get("todo_id")) for item in items if isinstance(item, dict) and item.get("todo_id") and str(item.get("status", "pending")) != "completed"]


def _reason(code: str, severity: str, message: str, evidence: dict) -> dict:
    return {"code": code, "severity": severity, "message": message, "evidence": evidence}


def _required_artifacts(user_request: str, workspace) -> list[dict]:
    """只识别用户以代码格式明确指定的输出文件，避免从自然语言猜测路径。"""
    if workspace is None:
        return []
    action_pattern = r"(?:创建|生成|写入|输出|create|generate|write)\s*(?:文件)?\s*`([^`]+)`"
    paths = []
    for match in re.finditer(action_pattern, str(user_request), flags=re.IGNORECASE):
        path = match.group(1).replace("\\", "/").strip()
        if path and path not in paths:
            paths.append(path)
    artifacts = []
    for path in paths:
        try:
            exists = workspace.resolve_path(path).is_file()
        except (OSError, ValueError):
            exists = False
        artifacts.append({"path": path, "exists": exists})
    return artifacts
