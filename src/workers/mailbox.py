from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkerMailbox:
    messages: list[str] = field(default_factory=list)

    def send(self, message: str) -> None:
        self.messages.append(message)
