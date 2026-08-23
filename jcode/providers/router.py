from __future__ import annotations


class ModelRouter:
    def __init__(self, client):
        self.client = client

    def complete(self, prompt: str, *, max_tokens: int, temperature: float):
        return self.client.complete(
            [{"role": "user", "content": prompt}],
            model=self.client.model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
