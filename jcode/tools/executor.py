from __future__ import annotations

from pydantic import ValidationError

from jcode.policy.decisions import PolicyDecision
from jcode.tools.base import ToolCallRequest, ToolInvocation, ToolResult
from jcode.tools.workspace import read_file


class ToolExecutor:
    workspace: object
    registry: object
    permissions: object
    tool_policy: object
    sandbox: object
    call_guard: object
    redactor: object

    def __init__(self, *, workspace, registry, permissions, tool_policy, sandbox, call_guard, redactor):
        self.workspace = workspace
        self.registry = registry
        self.permissions = permissions
        self.tool_policy = tool_policy
        self.sandbox = sandbox
        self.call_guard = call_guard
        self.redactor = redactor

    def execute(self, name: str, args: dict, *, working_memory, run_id: str = "", source: str = "model") -> ToolResult:
        request = ToolCallRequest(name=name, raw_args=dict(args or {}), run_id=run_id, source=source)
        tool = self.registry.get(request.name)
        if tool is None:
            decision = PolicyDecision.deny("unknown_tool", f"error: unknown tool {request.name}", layer="tool_lookup")
            return self._denied(decision, request=request)
        try:
            parsed = tool.schema.model_validate(request.raw_args)
        except ValidationError as exc:
            decision = PolicyDecision.deny(
                "invalid_arguments",
                f"error: invalid arguments for {request.name}: {exc}",
                layer="validation",
                metadata={"error_type": type(exc).__name__},
            )
            return self._denied(decision, request=request, tool=tool)
        parsed_args = parsed.model_dump()
        invocation = ToolInvocation(request=request, tool=tool, parsed_args=parsed_args, read_only=tool.read_only, risky=tool.risky)
        if self.call_guard.repeated(request.name, parsed_args):
            decision = PolicyDecision.deny(
                "repeated_identical_call",
                f"error: repeated identical tool call for {request.name}",
                layer="call_guard",
            )
            return self._denied(decision, invocation=invocation)
        try:
            policy = self.tool_policy.check(tool, parsed_args, working_memory)
        except Exception as exc:
            decision = PolicyDecision.deny("path_escape", f"error: {exc}", layer="tool_policy", metadata={"error_type": type(exc).__name__})
            return self._denied(decision, invocation=invocation)
        if not policy.allowed:
            return self._denied(policy, invocation=invocation)
        permission = self.permissions.decide(tool, parsed_args)
        if not permission.allowed:
            return self._denied(permission, invocation=invocation)
        sandbox_decision = PolicyDecision.allow("not_shell", layer="sandbox", metadata={"skipped": True})
        if request.name == "run_shell":
            sandbox_decision = self.sandbox.decide_shell()
            if not sandbox_decision.allowed:
                return self._denied(sandbox_decision, invocation=invocation)
        before = self.workspace.snapshot() if tool.risky else {}
        try:
            result = read_file(self.workspace, working_memory, parsed) if request.name == "read_file" else tool.execute(self.workspace, parsed)
        except Exception as exc:
            after = self.workspace.snapshot() if tool.risky else before
            changed = sorted(set(after) ^ set(before))
            status = "partial_success" if changed else "error"
            code = "tool_partial_success" if changed else "tool_failed"
            metadata = self._metadata(invocation, [policy, permission, sandbox_decision], {"decision": "execution"})
            return ToolResult(status, f"error: tool {request.name} failed: {exc}", changed_files=changed, error_type=code, metadata=metadata, decision="executed")
        after = self.workspace.snapshot() if tool.risky else before
        changed = sorted(set(after) ^ set(before))
        if changed and not result.changed_files:
            result.changed_files = changed
        result.text = self.redactor.redact(result.text)
        result.decision = "executed"
        result.metadata.update(self._metadata(invocation, [policy, permission, sandbox_decision], {"decision": "executed", "workspace_changed": bool(result.changed_files)}))
        return result

    def _denied(self, decision: PolicyDecision, *, request: ToolCallRequest | None = None, tool=None, invocation: ToolInvocation | None = None) -> ToolResult:
        if invocation is None and request is not None and tool is not None:
            invocation = ToolInvocation(request=request, tool=tool, parsed_args={}, read_only=tool.read_only, risky=tool.risky)
        metadata = {"policy": [decision.to_dict()], "decision": decision.layer or decision.decision}
        if invocation is not None:
            metadata.update(invocation.metadata())
        elif request is not None:
            metadata.update({"tool_name": request.name, "source": request.source, "run_id": request.run_id})
        return ToolResult("denied", decision.message, error_type=decision.reason, metadata=metadata, decision=decision.decision)

    @staticmethod
    def _metadata(invocation: ToolInvocation, decisions: list[PolicyDecision], extra: dict | None = None) -> dict:
        metadata = invocation.metadata()
        metadata["policy"] = [decision.to_dict() for decision in decisions]
        metadata.update(dict(extra or {}))
        return metadata
