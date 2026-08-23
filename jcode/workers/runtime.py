from __future__ import annotations

from jcode.workers.result import WorkerResult


class WorkerRuntime:
    def __init__(self, worker_id: str, prompt: str):
        self.worker_id = worker_id
        self.prompt = prompt
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
        self.result = "Subagent completed task brief:\n" + body[:2000]
        return WorkerResult(self.worker_id, self.status, self.result)
