from __future__ import annotations

from src.workers.result import WorkerResult


class WorkerRuntime:
    worker_id: str
    prompt: str
    subagent_type: str
    write_scope: list[str]
    messages: list[str]
    status: str
    result: str

    def __init__(self, worker_id: str, prompt: str, subagent_type: str = "worker", write_scope: list[str] | None = None):
        self.worker_id = worker_id
        self.prompt = prompt
        self.subagent_type = subagent_type
        self.write_scope = list(write_scope or [])
        self.messages: list[str] = []
        self.status = "created"
        self.result = ""

    def send(self, message: str) -> None:
        self.messages.append(message)

    def run(self) -> WorkerResult:
        self.status = "completed"
        body = self.prompt
        if self.messages:
            body += "\nMessages:\n" + "\n".join(self.messages)
        self.result = f"Subagent[{self.subagent_type}] completed task brief:\n" + body[:2000]
        return WorkerResult(self.worker_id, self.status, self.result)
