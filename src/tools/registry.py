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


class ToolRegistry:
    tools: dict[str, Tool]

    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)


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
    registry.register(Tool("ask_user", AskUserArgs, tool_ask_user, read_only=False, description="Ask the interactive user a blocking clarification question."))
    registry.register(Tool("enter_plan_mode", EnterPlanModeArgs, tool_enter_plan_mode, read_only=False, description="Enter plan mode for a named planning topic."))
    registry.register(Tool("exit_plan_mode", ExitPlanModeArgs, tool_exit_plan_mode, read_only=False, description="Exit plan mode and return to default runtime mode."))
    registry.register(Tool("spawn_subagent", SpawnSubagentArgs, tool_spawn_subagent, read_only=False, description="Spawn a subagent worker for a scoped task."))
    registry.register(Tool("send_subagent_message", SendSubagentMessageArgs, tool_send_subagent_message, read_only=False, description="Send a message to an existing subagent worker."))
    registry.register(Tool("wait_subagent", WaitSubagentArgs, tool_wait_subagent, read_only=False, description="Wait for a subagent worker to finish and collect its result."))
    return registry
