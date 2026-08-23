from __future__ import annotations

from jcode.tools.base import Tool
from jcode.tools.patch import apply_text_patch
from jcode.tools.schemas import ApplyPatchArgs, ListFilesArgs, ReadFileArgs, RunShellArgs, SearchArgs, WriteFileArgs
from jcode.tools.shell import run_shell
from jcode.tools.workspace import list_files, read_file, search, write_file


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)


def build_default_registry(workspace) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("read_file", ReadFileArgs, lambda args, _wm=None: read_file(workspace, _wm, args), read_only=True))
    registry.register(Tool("write_file", WriteFileArgs, lambda args: write_file(workspace, args), read_only=False, risky=True))
    registry.register(Tool("apply_patch", ApplyPatchArgs, lambda args: apply_text_patch(workspace, args), read_only=False, risky=True))
    registry.register(Tool("list_files", ListFilesArgs, lambda args: list_files(workspace, args), read_only=True))
    registry.register(Tool("search", SearchArgs, lambda args: search(workspace, args), read_only=True))
    registry.register(Tool("run_shell", RunShellArgs, lambda args: run_shell(workspace, args), read_only=False, risky=True))
    registry.workspace = workspace
    return registry
