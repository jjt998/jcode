from __future__ import annotations

from pydantic import BaseModel, Field


class ReadFileArgs(BaseModel):
    path: str
    max_chars: int = Field(default=20000, ge=1, le=200000)


class WriteFileArgs(BaseModel):
    path: str
    content: str


class ApplyPatchArgs(BaseModel):
    path: str
    old_text: str = Field(min_length=1)
    new_text: str


class ListFilesArgs(BaseModel):
    path: str = "."
    recursive: bool = False
    max_entries: int = Field(default=200, ge=1, le=2000)


class SearchArgs(BaseModel):
    query: str = Field(min_length=1)
    path: str = "."
    max_results: int = Field(default=50, ge=1, le=500)


class RunShellArgs(BaseModel):
    command: str = Field(min_length=1)
    timeout: int = Field(default=60, ge=1, le=600)


class TodoAddArgs(BaseModel):
    content: str = Field(min_length=1)
    status: str = "pending"
    priority: str = "normal"
    note: str = ""


class TodoUpdateArgs(BaseModel):
    todo_id: str = Field(min_length=1)
    status: str | None = None
    content: str | None = None
    priority: str | None = None
    note: str | None = None


class TodoListArgs(BaseModel):
    pass


class AskUserArgs(BaseModel):
    question: str = Field(min_length=1)
    choices: list[str] = Field(default_factory=list)


class EnterPlanModeArgs(BaseModel):
    topic: str = Field(min_length=1)
    path: str | None = None


class ExitPlanModeArgs(BaseModel):
    pass


class SpawnSubagentArgs(BaseModel):
    prompt: str = Field(min_length=1)
    subagent_type: str = "worker"
    write_scope: list[str] = Field(default_factory=list)


class SendSubagentMessageArgs(BaseModel):
    worker_id: str
    message: str = Field(min_length=1)


class WaitSubagentArgs(BaseModel):
    worker_id: str
