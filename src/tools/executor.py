from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from src.evidence.tool_artifacts import is_tool_result_artifact
from src.policy.decisions import PolicyDecision
from src.tools.base import ToolCallRequest, ToolInvocation, ToolResult
from src.tools.workspace import freshness

RUNTIME_TOOL_NAMES = {"todo_add", "todo_update", "todo_list", "ask_user", "enter_plan_mode", "exit_plan_mode"}

if TYPE_CHECKING:
    from src.memory.working import WorkingMemory
    from src.policy.call_guard import CallGuard
    from src.policy.permissions import PermissionChecker
    from src.policy.sandbox import SandboxPolicy
    from src.policy.secrets import SecretRedactor
    from src.policy.tool_profiles import ToolSetProfile
    from src.policy.tool_rules import ToolPolicyChecker
    from src.state.workspace import Workspace
    from src.tools.registry import ToolRegistry


class ToolExecutor:
    workspace: Workspace
    registry: ToolRegistry
    permissions: PermissionChecker
    tool_policy: ToolPolicyChecker
    sandbox: SandboxPolicy
    call_guard: CallGuard
    redactor: SecretRedactor

    def __init__(self, *, workspace, registry, permissions, tool_policy, sandbox, call_guard, redactor):
        self.workspace = workspace
        self.registry = registry
        self.permissions = permissions
        self.tool_policy = tool_policy
        self.sandbox = sandbox
        self.call_guard = call_guard
        self.redactor = redactor

    def execute(
        self,
        name: str,
        args: dict,
        *,
        working_memory: WorkingMemory,
        tool_profile: ToolSetProfile | None = None,
        write_scope: list[str] | None = None,
        runtime_mode: str = "default",
        plan_path: str = "",
        run_id: str = "",
        source: str = "model",
        runtime: object | None = None,
    ) -> ToolResult:
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
        profile_decision = self._check_profile(invocation, tool_profile)
        if not profile_decision.allowed:
            return self._denied(profile_decision, invocation=invocation)
        scope_decision = self._check_write_scope(invocation, write_scope)
        if not scope_decision.allowed:
            return self._denied(scope_decision, invocation=invocation)
        if request.name == "read_file":
            read_guard = self._check_read_file_repeat(invocation, working_memory)
            if not read_guard.allowed:
                return self._denied(read_guard, invocation=invocation)
        else:
            call_context = ""
            if request.name == "run_shell":
                # fingerprint 只覆盖有限工作区元数据，可能漏掉深层或高频变更；这里接受该近似以低成本放开修改后的测试重跑。
                call_context = self.workspace.fingerprint()
            if self.call_guard.repeated(request.name, parsed_args, context_key=call_context):
                decision = PolicyDecision.deny(
                    "repeated_identical_call",
                    f"error: repeated identical tool call for {request.name + ':' + str(parsed_args)}",
                    layer="call_guard",
                )
                return self._denied(decision, invocation=invocation)
        if request.name in RUNTIME_TOOL_NAMES:
            return self._execute_runtime_tool(runtime, invocation, parsed_args)
        try:
            policy = self.tool_policy.check(
                tool,
                parsed_args,
                working_memory,
                runtime_mode=runtime_mode,
                plan_path=plan_path,
            )
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
        # 工具执行前后对比工作区状态，记录变更文件列表，确保记录到工具副作用。
        before = self.workspace.snapshot() if tool.risky else {}
        try:
            if request.name == "read_file":
                # read_file 需要记录文件新鲜度，所以参数多一个working_memory，其它工具仍保持 workspace + args 的通用签名。
                result = tool.execute(self.workspace, parsed, working_memory=working_memory)
                # artifact 读取由上游决定渲染与外置策略，read_file 本身只负责读取文件内容。
                if is_tool_result_artifact(parsed_args.get("path", "")):
                    result.metadata["artifact_read"] = True
            else:
                result = tool.execute(self.workspace, parsed)
        except Exception as exc:
            after = self.workspace.snapshot() if tool.risky else before
            changed = sorted(set(after) ^ set(before))
            status = "partial_success" if changed else "error"
            code = "tool_partial_success" if changed else "tool_failed"
            metadata = self._metadata(invocation, [profile_decision, scope_decision, policy, permission, sandbox_decision], {"decision": "execution"})
            return ToolResult(status, f"error: tool {request.name} failed: {exc}", changed_files=changed, error_type=code, metadata=metadata, decision="executed")
        after = self.workspace.snapshot() if tool.risky else before
        changed = sorted(set(after) ^ set(before))
        if changed and not result.changed_files:
            result.changed_files = changed
        result.text = self.redactor.redact(result.text)
        result.decision = "executed"
        result.metadata.update(
            self._metadata(
                invocation,
                [profile_decision, scope_decision, policy, permission, sandbox_decision],
                {"decision": "executed", "workspace_changed": bool(result.changed_files)},
            )
        )
        return result

    def _execute_runtime_tool(self, runtime: object | None, invocation: ToolInvocation, parsed_args: dict) -> ToolResult:
        if runtime is None:
            decision = PolicyDecision.deny(
                "runtime_context_required",
                f"error: runtime context is required for {invocation.tool.name}",
                layer="tool_execution",
            )
            return self._denied(decision, invocation=invocation)
        try:
            if invocation.tool.name == "todo_add":
                text = runtime.todo_add(parsed_args)
            elif invocation.tool.name == "todo_update":
                text = runtime.todo_update(parsed_args)
            elif invocation.tool.name == "todo_list":
                text = runtime.todo_list(parsed_args)
            elif invocation.tool.name == "ask_user":
                text = runtime.ask_user(str(parsed_args.get("question", "")), choices=list(parsed_args.get("choices", []) or []))
            elif invocation.tool.name == "enter_plan_mode":
                text = runtime.enter_plan_mode(str(parsed_args.get("topic", "")), path=parsed_args.get("path"))
            elif invocation.tool.name == "exit_plan_mode":
                text = runtime.exit_plan_mode()
            else:
                decision = PolicyDecision.deny(
                    "runtime_tool_unknown",
                    f"error: unsupported runtime tool {invocation.tool.name}",
                    layer="tool_execution",
                )
                return self._denied(decision, invocation=invocation)
        except Exception as exc:
            decision = PolicyDecision.deny(
                "runtime_tool_failed",
                f"error: runtime tool {invocation.tool.name} failed: {exc}",
                layer="tool_execution",
                metadata={"error_type": type(exc).__name__},
            )
            return self._denied(decision, invocation=invocation)
        text = str(text)
        text = self.redactor.redact(text)
        status = "error" if text.startswith("error:") else "success"
        error_type = None if status == "success" else "runtime_tool_failed"
        result = ToolResult(status, text, error_type=error_type)
        result.decision = "executed"
        result.metadata.update(self._metadata(invocation, [PolicyDecision.allow("runtime_tool_ok", layer="tool_execution")], {"decision": "executed", "runtime_tool": True}))
        return result

    def _check_read_file_repeat(self, invocation: ToolInvocation, working_memory: WorkingMemory) -> PolicyDecision:
        # read_file 以“路径 + 参数 + 范围 + 文件版本”为单位计数，同一份老文件的相同读法才算重复。
        try:
            path = self.workspace.resolve_path(invocation.parsed_args.get("path", ""))
            relpath = self.workspace.relpath(path)
            current_freshness = freshness(path)
        except Exception:
            return PolicyDecision.allow("read_file_repeat_check_skipped", layer="call_guard", metadata={"tool_name": invocation.tool.name})

        if working_memory.read_file_count(relpath, invocation.parsed_args, current_freshness) >= 3:
            return PolicyDecision.deny(
                "repeated_old_file_read",
                "error: You've already reread this unchanged file three times. Please avoid repeatedly reading content you already know. If you still need the missing result, you can call ask_user to decide the next step.",
                layer="call_guard",
                metadata={"path": relpath, "freshness": current_freshness},
            )
        return PolicyDecision.allow(
            "read_file_repeat_allowed",
            layer="call_guard",
            metadata={"path": relpath, "freshness": current_freshness, "read_count": working_memory.read_file_count(relpath, invocation.parsed_args, current_freshness)},
        )

    def _check_profile(self, invocation: ToolInvocation, tool_profile: ToolSetProfile | None) -> PolicyDecision:
        if tool_profile is None or tool_profile.allows(invocation.tool.name):
            return PolicyDecision.allow(
                "tool_profile_allowed",
                layer="tool_profile",
                metadata={"tool_profile": getattr(tool_profile, "name", "none")},
            )
        return PolicyDecision.deny(
            "tool_profile_denied",
            f"error: tool {invocation.tool.name} is not allowed by profile {tool_profile.name}",
            layer="tool_profile",
            metadata={"tool_profile": tool_profile.name},
        )

    def _check_write_scope(self, invocation: ToolInvocation, write_scope: list[str] | None) -> PolicyDecision:
        scopes = list(write_scope or [])
        if not scopes or invocation.tool.name not in {"write_file", "apply_patch"}:
            return PolicyDecision.allow("write_scope_not_required", layer="tool_profile", metadata={"write_scope": scopes})
        target_value = invocation.parsed_args.get("path", "")
        try:
            target = self.workspace.resolve_path(target_value)
            allowed_roots = [self.workspace.resolve_path(scope) for scope in scopes]
        except Exception as exc:
            return PolicyDecision.deny(
                "write_scope_mismatch",
                f"error: write target is outside allowed scope: {target_value}",
                layer="tool_profile",
                metadata={"error_type": type(exc).__name__, "write_scope": scopes},
            )
        for allowed_root in allowed_roots:
            if target == allowed_root or allowed_root in target.parents:
                return PolicyDecision.allow("write_scope_allowed", layer="tool_profile", metadata={"write_scope": scopes})
        return PolicyDecision.deny(
            "write_scope_mismatch",
            f"error: write target is outside allowed scope: {target_value}",
            layer="tool_profile",
            metadata={"write_scope": scopes},
        )

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
