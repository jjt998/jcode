from __future__ import annotations


class ModelRouter:
    def __init__(self, client):
        self.client = client

    def complete(self, context: str, *, max_tokens: int, temperature: float):
        return self.client.complete(
            [{"role": "user", "content": context}],
            model=self.client.model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
