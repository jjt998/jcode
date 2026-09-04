from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from src.context.budget import estimate_tokens
from src.context.result import ContextResult
from src.providers.base import ModelResponse, ProviderRequestError
from src.providers.deepseek import DeepSeekClient
from src.providers.profiles import ModelProfile


class MiniMaxClient(DeepSeekClient):
    """使用 MiniMax OpenAI Responses API 的原生工具调用适配器。"""

    _EXTRA_FIELDS = frozenset({"service_tier", "top_p", "metadata", "prompt_cache_key", "text"})

    def __init__(self, profile: ModelProfile):
        super().__init__(profile)

    def complete(self, context: ContextResult, *, model: str, max_tokens: int, temperature: float, model_profile: dict | None = None) -> ModelResponse:
        if not self.api_key:
            return ModelResponse(
                text="JCode is configured without an API key. The context was built but no provider request was sent.",
                finish_reason="missing_api_key",
                input_tokens=self._estimate_context_tokens(context),
            )
        payload = self._compile_request(context, model=model, max_tokens=max_tokens, temperature=temperature, model_profile=model_profile)
        request = urllib.request.Request(
            self.base_url + "/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ProviderRequestError(
                f"minimax responses error {exc.code}: {body[:500]}",
                status_code=int(exc.code),
                retry_after_seconds=self._retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None),
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
            raise ProviderRequestError(f"minimax transport error: {str(exc)[:500]}", transport_error=True) from exc
        return self._parse_response(data)

    def _compile_request(self, context: ContextResult, *, model: str, max_tokens: int, temperature: float, model_profile: dict | None) -> dict:
        """按 MiniMax 的推理与温度互斥规则编译请求。"""
        options = dict(model_profile or self.profile.snapshot())
        payload: dict[str, object] = {
            "model": model,
            "instructions": context.prefix,
            "input": self._compile_input(context),
            "tools": [
                {"type": "function", "name": tool.name, "description": tool.description, "parameters": tool.parameters}
                for tool in context.tools
            ],
            "max_output_tokens": max_tokens,
        }
        if bool(options.get("thinking_enabled", False)):
            payload["reasoning"] = {"effort": str(options["reasoning_effort"])}
        else:
            if not 0 < temperature <= 1:
                raise ValueError("MiniMax temperature must be in (0, 1]")
            payload["temperature"] = temperature
        payload.update({key: value for key, value in self.profile.extra.items() if key in self._EXTRA_FIELDS})
        return payload

    @staticmethod
    def _estimate_context_tokens(context: ContextResult) -> int:
        text = context.prefix + context.skill + context.current_request + context.working_memory.render()
        return estimate_tokens(text + "\n".join(event.content for event in context.history))
