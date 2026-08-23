from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ToolResult:
    status: str
    text: str
    artifacts: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    error_type: str | None = None
    metadata: dict = field(default_factory=dict)
    decision: str = "executed"

    @property
    def ok(self) -> bool:
        return self.status in {"success", "partial_success"}


@dataclass(frozen=True)
class ToolCallRequest:
    name: str
    raw_args: dict
    run_id: str = ""
    source: str = "model"


@dataclass(frozen=True)
class ToolInvocation:
    request: ToolCallRequest
    tool: "Tool"
    parsed_args: dict
    read_only: bool
    risky: bool

    def metadata(self) -> dict:
        return {
            "tool_name": self.tool.name,
            "read_only": self.read_only,
            "risky": self.risky,
            "source": self.request.source,
            "run_id": self.request.run_id,
        }


@dataclass
class Tool:
    name: str
    schema: type
    execute: Callable[..., ToolResult]
    read_only: bool = True
    risky: bool = False
    description: str = ""
