from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorkerResult:
    worker_id: str
    status: str
    text: str
