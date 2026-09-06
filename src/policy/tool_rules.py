from __future__ import annotations

from typing import TYPE_CHECKING

from src.policy.decisions import PolicyDecision

if TYPE_CHECKING:
    from src.memory.working import WorkingMemory
    from src.state.workspace import Workspace
    from src.tools.base import Tool


class ToolPolicyChecker:
    workspace: Workspace

    def __init__(self, workspace):
        self.workspace = workspace

    def check(self, tool: Tool, args: dict, working_memory: WorkingMemory, *, runtime_mode: str = "default", plan_path: str = "") -> PolicyDecision:
        path = args.get("path") or args.get("file")
        rel = self.workspace.relpath(self.workspace.resolve_path(path)) if path else ""
        normalized_plan_path = self.workspace.relpath(self.workspace.resolve_path(plan_path)) if plan_path else ""
        if tool.name == "write_file":
            plan_mode_write = runtime_mode == "plan" and rel == normalized_plan_path
            # 新文件允许直接创建；已有文件要求读取版本仍然有效，或由当前 JCode run 刚刚写入。
            target_exists = bool(path) and self.workspace.resolve_path(path).is_file()
            if target_exists and rel not in working_memory.file_freshness and not plan_mode_write:
                return PolicyDecision.deny("read_before_write", f"error: read {rel} before modifying it", layer="tool_policy")
            if target_exists and not plan_mode_write:
                expected = str(working_memory.file_freshness.get(rel, ""))
                try:
                    from src.tools.workspace import freshness
                    current = freshness(self.workspace.resolve_path(rel))
                except OSError:
                    current = ""
                if expected and current and expected != current:
                    working_memory.runtime_state.setdefault("external_stale_paths", {})[rel] = {"read_freshness": expected, "current_freshness": current}
                    working_memory.note_safety(f"该文件被外部势力改动了，请你重读后做下一步打算：{rel}")
                    return PolicyDecision.deny("write_conflict", f"该文件被外部势力改动了，请你重读后做下一步打算：{rel}", layer="tool_policy", metadata={"stale": True, "stale_reason": "external_file_changed", "path": rel, "read_freshness": expected, "current_freshness": current})
        if tool.name == "apply_patch":
            plan_mode_write = runtime_mode == "plan" and rel == normalized_plan_path
            if rel and rel not in working_memory.file_freshness and not plan_mode_write:
                return PolicyDecision.deny("read_before_write", f"error: read {rel} before modifying it", layer="tool_policy")
        if tool.name == "apply_patch" and args.get("old_text") == args.get("new_text"):
            return PolicyDecision.deny("empty_patch", "error: patch old_text and new_text are identical", layer="tool_policy")
        if tool.name == "run_shell":
            timeout = int(args.get("timeout", 60))
            if timeout < 1 or timeout > 600:
                return PolicyDecision.deny("timeout_out_of_range", "error: timeout must be between 1 and 600 seconds", layer="tool_policy")
        return PolicyDecision.allow("tool_policy_ok", layer="tool_policy", risk="high" if tool.risky else "low")
