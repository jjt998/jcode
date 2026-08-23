from __future__ import annotations

from pydantic import ValidationError

from jcode.tools.base import ToolResult
from jcode.tools.workspace import read_file


class ToolExecutor:
    def __init__(self, *, registry, permissions, tool_policy, sandbox, call_guard, redactor, working_memory):
        self.registry = registry
        self.permissions = permissions
        self.tool_policy = tool_policy
        self.sandbox = sandbox
        self.call_guard = call_guard
        self.redactor = redactor
        self.working_memory = working_memory

    def execute(self, name: str, args: dict) -> ToolResult:
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult("denied", f"error: unknown tool {name}", error_type="unknown_tool")
        try:
            parsed = tool.schema.model_validate(args)
        except ValidationError as exc:
            return ToolResult("denied", f"error: invalid arguments for {name}: {exc}", error_type="invalid_arguments")
        if self.call_guard.repeated(name, parsed.model_dump()):
            return ToolResult("denied", f"error: repeated identical tool call for {name}", error_type="repeated_identical_call")
        try:
            policy = self.tool_policy.check(tool, parsed.model_dump())
        except Exception as exc:
            return ToolResult("denied", f"error: {exc}", error_type="path_escape")
        if not policy.allowed:
            return ToolResult("denied", policy.message, error_type=policy.reason)
        permission = self.permissions.check(tool)
        if not permission.allowed:
            return ToolResult("denied", f"error: permission denied for {name}: {permission.reason}", error_type=permission.reason)
        if name == "run_shell":
            ok, message = self.sandbox.check_shell()
            if not ok:
                return ToolResult("denied", f"error: {message}", error_type="sandbox_required")
        before = self.registry.workspace.snapshot() if tool.risky else {}
        try:
            result = read_file(self.registry.workspace, self.working_memory, parsed) if name == "read_file" else tool.execute(parsed)
        except Exception as exc:
            after = self.registry.workspace.snapshot() if tool.risky else before
            changed = sorted(set(after) ^ set(before))
            status = "partial_success" if changed else "error"
            code = "tool_partial_success" if changed else "tool_failed"
            return ToolResult(status, f"error: tool {name} failed: {exc}", changed_files=changed, error_type=code)
        after = self.registry.workspace.snapshot() if tool.risky else before
        changed = sorted(set(after) ^ set(before))
        if changed and not result.changed_files:
            result.changed_files = changed
        result.text = self.redactor.redact(result.text)
        return result
