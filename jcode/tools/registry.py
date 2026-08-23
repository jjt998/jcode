from __future__ import annotations

from jcode.tools.base import Tool
from jcode.tools.patch import apply_text_patch
from jcode.tools.schemas import ApplyPatchArgs, ListFilesArgs, ReadFileArgs, RunShellArgs, SearchArgs, WriteFileArgs
from jcode.tools.shell import run_shell
from jcode.tools.workspace import list_files, read_file, search, write_file


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
    return registry
