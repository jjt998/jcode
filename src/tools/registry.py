from __future__ import annotations

from src.tools.base import Tool
from src.tools.ask_user import tool_ask_user
from src.tools.plan import tool_enter_plan_mode, tool_exit_plan_mode
from src.tools.schemas import (
    ApplyPatchArgs,
    AskUserArgs,
    EnterPlanModeArgs,
    ExitPlanModeArgs,
    ListFilesArgs,
    ReadFileArgs,
    RunShellArgs,
    SearchArgs,
    SendSubagentMessageArgs,
    SpawnSubagentArgs,
    TodoAddArgs,
    TodoArchiveArgs,
    TodoDeleteArgs,
    TodoListArgs,
    TodoUpdateArgs,
    WaitSubagentArgs,
    WriteFileArgs,
)
from src.tools.subagents import tool_send_subagent_message, tool_spawn_subagent, tool_wait_subagent
from src.tools.shell import run_shell
from src.tools.todos import tool_todo_add, tool_todo_archive, tool_todo_delete, tool_todo_list, tool_todo_update
from src.tools.workspace import apply_text_patch, list_files, read_file, search, write_file
from src.context.result import ToolDefinition


class ToolRegistry:
    tools: dict[str, Tool]

    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def definitions(self, allowed_tools: frozenset[str] | None = None) -> list[ToolDefinition]:
        """导出当前工具集可见的原生函数定义。"""
        allowed = allowed_tools if allowed_tools is not None else frozenset(self.tools)
        definitions: list[ToolDefinition] = []
        for name in sorted(allowed):
            tool = self.tools.get(name)
            if tool is None:
                continue
            schema = tool.schema.model_json_schema() if hasattr(tool.schema, "model_json_schema") else {}
            definitions.append(
                ToolDefinition(
                    name=tool.name,  # 工具函数名称
                    description=tool.description,  # 工具说明
                    parameters=schema,  # Pydantic 导出的 JSON Schema
                    read_only=tool.read_only,  # 只读标记
                    risky=tool.risky,  # 风险标记
                )
            )
        return definitions


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("read_file", ReadFileArgs, read_file, read_only=True, description="Read workspace text. Read a file before editing; use start/end for large or artifact files, and do not assume a truncated result is complete."))
    registry.register(Tool("write_file", WriteFileArgs, write_file, read_only=False, risky=True, description="Create or fully replace one workspace file. Existing files must be read first; content is the complete file, not a patch."))
    registry.register(Tool("apply_patch", ApplyPatchArgs, apply_text_patch, read_only=False, risky=True, description="Replace one exact text span in an existing workspace file. old_text/new_text are strings; old_text must come verbatim from the latest read_file result, including line endings."))
    registry.register(Tool("list_files", ListFilesArgs, list_files, read_only=True, description="List workspace directory entries. The result can be capped; use read_file for file contents."))
    registry.register(Tool("search", SearchArgs, search, read_only=True, description="Search ordinary text in workspace files. The result can be capped and does not support regex or glob semantics."))
    registry.register(Tool("run_shell", RunShellArgs, run_shell, read_only=False, risky=True, description="Run one necessary shell command in the workspace. It may change files even when it fails; inspect affected files afterward and avoid unchanged repeats."))
    registry.register(Tool("todo_add", TodoAddArgs, tool_todo_add, read_only=False, description="Add one short, concrete, verifiable task to the current session. Do not duplicate an existing task; status is pending, in_progress, or completed only."))
    registry.register(Tool("todo_update", TodoUpdateArgs, tool_todo_update, read_only=False, description="Update fields of one existing current-session todo. Use in_progress when starting and completed only after verification; do not use status for deletion or archiving."))
    registry.register(Tool("todo_list", TodoListArgs, tool_todo_list, read_only=True, description="List active todos in the current session and their progress. Archived todos are excluded; this has no parameters and does not inspect files."))
    registry.register(Tool("todo_delete", TodoDeleteArgs, tool_todo_delete, read_only=False, description="Permanently remove one todo from the current session ledger. This does not erase historical audit events and cannot be undone."))
    registry.register(Tool("todo_archive", TodoArchiveArgs, tool_todo_archive, read_only=False, description="Archive one todo while retaining its record. It leaves active lists, Working Memory, and final completion checks; restoration is not supported."))
    registry.register(Tool("ask_user", AskUserArgs, tool_ask_user, read_only=False, description="Ask the user one question and wait for a free-text answer. choices must be a string array, never objects; ask one question per call."))
    registry.register(Tool("enter_plan_mode", EnterPlanModeArgs, tool_enter_plan_mode, read_only=False, description="Enter plan mode for one explicit topic; optionally bind a workspace plan file."))
    registry.register(Tool("exit_plan_mode", ExitPlanModeArgs, tool_exit_plan_mode, read_only=False, description="Exit plan mode. This tool takes no arguments."))
    registry.register(Tool("spawn_subagent", SpawnSubagentArgs, tool_spawn_subagent, read_only=False, description="Start one scoped subagent task. write_scope limits where it may write; do not treat an unverified result as proof that files or tests changed."))  # 子 Agent 生命周期治理后置
    registry.register(Tool("send_subagent_message", SendSubagentMessageArgs, tool_send_subagent_message, read_only=False, description="Send a concrete message to an existing subagent worker ID. Do not invent worker IDs."))  # 子 Agent 消息治理后置
    registry.register(Tool("wait_subagent", WaitSubagentArgs, tool_wait_subagent, read_only=False, description="Wait for an existing subagent worker ID and collect its result; verify reported changes before finalizing."))  # 子 Agent 等待治理后置
    return registry
