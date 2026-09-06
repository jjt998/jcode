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
    TodoListArgs,
    TodoUpdateArgs,
    WaitSubagentArgs,
    WriteFileArgs,
)
from src.tools.subagents import tool_send_subagent_message, tool_spawn_subagent, tool_wait_subagent
from src.tools.shell import run_shell
from src.tools.todos import tool_todo_add, tool_todo_list, tool_todo_update
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
    registry.register(Tool("read_file", ReadFileArgs, read_file, read_only=True, description="Read a UTF-8 text file inside the workspace."))
    registry.register(Tool("write_file", WriteFileArgs, write_file, read_only=False, risky=True, description="Create or replace a workspace file."))
    registry.register(Tool("apply_patch", ApplyPatchArgs, apply_text_patch, read_only=False, risky=True, description="Replace exact text in an existing workspace file."))
    registry.register(Tool("list_files", ListFilesArgs, list_files, read_only=True, description="List workspace files."))
    registry.register(Tool("search", SearchArgs, search, read_only=True, description="Search text in workspace files."))
    registry.register(Tool("run_shell", RunShellArgs, run_shell, read_only=False, risky=True, description="Run a shell command in the workspace."))
    registry.register(Tool("todo_add", TodoAddArgs, tool_todo_add, read_only=False, description="Add an item to the session todo ledger."))
    registry.register(Tool("todo_update", TodoUpdateArgs, tool_todo_update, read_only=False, description="Update an item in the session todo ledger."))
    registry.register(Tool("todo_list", TodoListArgs, tool_todo_list, read_only=True, description="List the session todo ledger."))
    registry.register(Tool("ask_user", AskUserArgs, tool_ask_user, read_only=False, description="If you are uncertain, consult the user."))
    registry.register(Tool("enter_plan_mode", EnterPlanModeArgs, tool_enter_plan_mode, read_only=False, description="Enter plan mode for a named planning topic."))
    registry.register(Tool("exit_plan_mode", ExitPlanModeArgs, tool_exit_plan_mode, read_only=False, description="Exit plan mode and return to default runtime mode."))
    registry.register(Tool("spawn_subagent", SpawnSubagentArgs, tool_spawn_subagent, read_only=False, description="Spawn a subagent worker for a scoped task."))  # 子 Agent 生命周期治理后置
    registry.register(Tool("send_subagent_message", SendSubagentMessageArgs, tool_send_subagent_message, read_only=False, description="Send a message to an existing subagent worker."))  # 子 Agent 消息治理后置
    registry.register(Tool("wait_subagent", WaitSubagentArgs, tool_wait_subagent, read_only=False, description="Wait for a subagent worker to finish and collect its result."))  # 子 Agent 等待治理后置
    return registry
