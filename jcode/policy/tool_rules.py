from __future__ import annotations

from typing import TYPE_CHECKING

from jcode.policy.decisions import PolicyDecision

if TYPE_CHECKING:
    from jcode.memory.working import WorkingMemory
    from jcode.state.workspace import Workspace
    from jcode.tools.base import Tool


class ToolPolicyChecker:
    workspace: Workspace

    def __init__(self, workspace):
        self.workspace = workspace

    def check(self, tool: Tool, args: dict, working_memory: WorkingMemory) -> PolicyDecision:
        path = args.get("path") or args.get("file")
        if path:
            self.workspace.resolve_path(path)
        if tool.name in {"write_file", "apply_patch"}:
            rel = str(path or "").replace("\\", "/")
            if rel and rel not in working_memory.file_freshness:
                return PolicyDecision.deny("read_before_write", f"error: read {rel} before modifying it", layer="tool_policy")
        if tool.name == "apply_patch" and args.get("old_text") == args.get("new_text"):
            return PolicyDecision.deny("empty_patch", "error: patch old_text and new_text are identical", layer="tool_policy")
        if tool.name == "run_shell":
            timeout = int(args.get("timeout", 60))
            if timeout < 1 or timeout > 600:
                return PolicyDecision.deny("timeout_out_of_range", "error: timeout must be between 1 and 600 seconds", layer="tool_policy")
        return PolicyDecision.allow("tool_policy_ok", layer="tool_policy", risk="high" if tool.risky else "low")
