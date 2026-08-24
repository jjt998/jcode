from __future__ import annotations

from src.providers.base import ModelClient, ModelResponse


class ModelRouter:
    client: ModelClient

    def __init__(self, client: ModelClient):
        self.client = client

    def complete(self, context: str, *, max_tokens: int, temperature: float) -> ModelResponse:
        return self.client.complete(
            [{"role": "user", "content": context}],
            model=self.client.model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
