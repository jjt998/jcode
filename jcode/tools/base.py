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


@dataclass
class Tool:
    name: str
    schema: type
    execute: Callable[[object], ToolResult]
    read_only: bool = True
    risky: bool = False
    description: str = ""
