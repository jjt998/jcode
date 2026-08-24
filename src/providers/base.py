from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ModelResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict | None = None


class ModelClient(Protocol):
    model: str

    def complete(self, messages: list[dict], *, model: str, max_tokens: int, temperature: float) -> ModelResponse:
        ...
