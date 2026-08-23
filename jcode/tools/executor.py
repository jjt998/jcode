from __future__ import annotations

from pydantic import ValidationError

from jcode.tools.base import ToolResult
from jcode.tools.workspace import read_file


class ToolExecutor:
    def __init__(self, *, workspace, registry, permissions, tool_policy, sandbox, call_guard, redactor):
        self.workspace = workspace
        self.registry = registry
        self.permissions = permissions
        self.tool_policy = tool_policy
        self.sandbox = sandbox
        self.call_guard = call_guard
        self.redactor = redactor

    def execute(self, name: str, args: dict, *, working_memory) -> ToolResult:
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult("denied", f"error: unknown tool {name}", error_type="unknown_tool", metadata={"decision": "tool_lookup"})
        try:
            parsed = tool.schema.model_validate(args)
        except ValidationError as exc:
            return ToolResult("denied", f"error: invalid arguments for {name}: {exc}", error_type="invalid_arguments", metadata={"decision": "validation"})
        parsed_args = parsed.model_dump()
        if self.call_guard.repeated(name, parsed_args):
            return ToolResult("denied", f"error: repeated identical tool call for {name}", error_type="repeated_identical_call", metadata={"decision": "call_guard"})
        try:
            policy = self.tool_policy.check(tool, parsed_args, working_memory)
        except Exception as exc:
            return ToolResult("denied", f"error: {exc}", error_type="path_escape", metadata={"decision": "tool_policy"})
        if not policy.allowed:
            return ToolResult("denied", policy.message, error_type=policy.reason, metadata={"decision": "tool_policy"})
        permission = self.permissions.check(tool, parsed_args)
        if not permission.allowed:
            return ToolResult("denied", f"error: permission denied for {name}: {permission.reason}", error_type=permission.reason, metadata={"decision": "permission"})
        if name == "run_shell":
            ok, message = self.sandbox.check_shell()
            if not ok:
                return ToolResult("denied", f"error: {message}", error_type="sandbox_required", metadata={"decision": "sandbox"})
        before = self.workspace.snapshot() if tool.risky else {}
        try:
            result = read_file(self.workspace, working_memory, parsed) if name == "read_file" else tool.execute(self.workspace, parsed)
        except Exception as exc:
            after = self.workspace.snapshot() if tool.risky else before
            changed = sorted(set(after) ^ set(before))
            status = "partial_success" if changed else "error"
            code = "tool_partial_success" if changed else "tool_failed"
            return ToolResult(status, f"error: tool {name} failed: {exc}", changed_files=changed, error_type=code, metadata={"decision": "execution"})
        after = self.workspace.snapshot() if tool.risky else before
        changed = sorted(set(after) ^ set(before))
        if changed and not result.changed_files:
            result.changed_files = changed
        result.text = self.redactor.redact(result.text)
        result.metadata.update(
            {
                "decision": "executed",
                "tool_name": name,
                "read_only": tool.read_only,
                "risky": tool.risky,
                "workspace_changed": bool(result.changed_files),
            }
        )
        return result
