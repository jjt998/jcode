from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReadFileArgs(BaseModel):
    path: str = Field(description="工作区内文件路径；必须先确认路径属于工作区。")
    max_chars: int = Field(default=20000, ge=1, le=200000, description="最多返回的字符数；结果可能被截断。")
    start: int = Field(default=0, ge=0, description="从文件字符位置开始读取。")
    end: int | None = Field(default=None, ge=0, description="结束字符位置（不含）；省略表示读到文件末尾。")


class WriteFileArgs(BaseModel):
    path: str = Field(description="工作区内目标文件路径。")
    content: str = Field(description="完整文件内容；不是补丁、差异或摘要。该操作会覆盖已有文件。")


class ApplyPatchArgs(BaseModel):
    path: str
    old_text: str = Field(min_length=1, description="内容和空白必须逐字取自最近一次 read_file 返回内容；LF 与 CRLF 仅作为传输换行差异等价处理。")
    new_text: str = Field(description="替换 old_text 的新文本，按原样写入。")


class ListFilesArgs(BaseModel):
    path: str = Field(default=".", description="工作区内要列出的目录路径。")
    recursive: bool = Field(default=False, description="是否递归列出子目录。")
    max_entries: int = Field(default=200, ge=1, le=2000, description="最多返回条目数；达到上限不代表目录已完整列出。")


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, description="非空普通文本搜索串；当前协议不支持正则或 glob。")
    path: str = Field(default=".", description="工作区内搜索根目录。")
    max_results: int = Field(default=50, ge=1, le=500, description="最多返回匹配项；达到上限不代表没有更多匹配。")


class RunShellArgs(BaseModel):
    command: str = Field(min_length=1, description="要在工作区执行的完整 shell 命令；失败时也可能产生部分修改。")
    timeout: int = Field(default=60, ge=1, le=600, description="超时秒数，范围 1 到 600。")


class TodoAddArgs(BaseModel):
    content: str = Field(min_length=1, description="短小、具体、可验证的任务；不要放长篇推理、日志或文件正文。")
    status: Literal["pending", "in_progress", "completed"] = Field(default="pending", description="任务状态，只能是 pending、in_progress 或 completed。")
    priority: str = Field(default="normal", description="任务标签；当前不改变执行顺序。")
    note: str = Field(default="", description="简短补充说明。")


class TodoUpdateArgs(BaseModel):
    todo_id: str = Field(min_length=1, description="当前 session 中已有 todo 的 ID，来自 todo_add 或 todo_list。")
    status: Literal["pending", "in_progress", "completed"] | None = Field(default=None, description="要更新的状态；完成必须使用 completed。")
    content: str | None = Field(default=None, description="新的短小任务文本；省略表示保持不变。")
    priority: str | None = Field(default=None, description="新的任务标签；省略表示保持不变。")
    note: str | None = Field(default=None, description="新的简短说明；省略表示保持不变。")


class TodoListArgs(BaseModel):
    pass


class TodoDeleteArgs(BaseModel):
    todo_id: str = Field(min_length=1, description="当前 session 中要永久移除的 todo ID。删除不删除历史审计记录。")


class TodoArchiveArgs(BaseModel):
    todo_id: str = Field(min_length=1, description="当前 session 中要归档的 todo ID。归档保留记录但不再进入 active 列表。")


class AskUserArgs(BaseModel):
    question: str = Field(min_length=1, description="一次要向用户提出的单个非空问题。")
    choices: list[str] = Field(default_factory=list, description="候选答案字符串数组；禁止传对象、label/description 对象或 DOM 节点。")


class EnterPlanModeArgs(BaseModel):
    topic: str = Field(min_length=1, description="明确的计划主题。")
    path: str | None = Field(default=None, description="可选的工作区计划文件路径。")


class ExitPlanModeArgs(BaseModel):
    pass


class SpawnSubagentArgs(BaseModel):
    prompt: str = Field(min_length=1, description="子 Agent 要完成的具体任务目标。")
    subagent_type: str = Field(default="worker", description="子 Agent 类型。")
    write_scope: list[str] = Field(default_factory=list, description="允许子 Agent 写入的工作区路径范围。")


class SendSubagentMessageArgs(BaseModel):
    worker_id: str = Field(description="已创建的子 Agent ID。")
    message: str = Field(min_length=1, description="发给已有子 Agent 的具体消息。")


class WaitSubagentArgs(BaseModel):
    worker_id: str = Field(description="已创建、需要等待的子 Agent ID。")
