from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolPolicyDecision:
    allowed: bool
    reason: str = ""
    message: str = ""


class ToolPolicyChecker:
    def __init__(self, workspace, working_memory):
        self.workspace = workspace
        self.working_memory = working_memory

    def check(self, tool, args: dict) -> ToolPolicyDecision:
        path = args.get("path") or args.get("file")
        if path:
            self.workspace.resolve_path(path)
        if tool.name in {"write_file", "apply_patch"}:
            rel = str(path or "").replace("\\", "/")
            if rel and rel not in self.working_memory.file_freshness:
                return ToolPolicyDecision(False, "read_before_write", f"error: read {rel} before modifying it")
        if tool.name == "apply_patch" and args.get("old_text") == args.get("new_text"):
            return ToolPolicyDecision(False, "empty_patch", "error: patch old_text and new_text are identical")
        if tool.name == "run_shell":
            timeout = int(args.get("timeout", 60))
            if timeout < 1 or timeout > 600:
                return ToolPolicyDecision(False, "timeout_out_of_range", "error: timeout must be between 1 and 600 seconds")
        return ToolPolicyDecision(True)
