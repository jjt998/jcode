from __future__ import annotations

import json
import urllib.error
import urllib.request

from src.context.budget import estimate_tokens
from src.providers.base import ModelResponse


class OpenAICompatibleClient:
    api_key: str
    base_url: str
    model: str

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def complete(self, messages: list[dict], *, model: str, max_tokens: int, temperature: float) -> ModelResponse:
        if not self.api_key:
            context = messages[-1].get("content", "") if messages else ""
            return ModelResponse(
                text="<final>JCode is configured without JCODE_API_KEY. The context was built and traced, but no provider request was sent.</final>",
                input_tokens=estimate_tokens(context),
                output_tokens=30,
            )
        payload = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"provider error {exc.code}: {body[:500]}") from exc
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        return ModelResponse(
            text=text,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            raw=data,
        )
