from __future__ import annotations

import json
import urllib.error
import urllib.request

from src.context.budget import estimate_tokens
from src.providers.base import ModelResponse
from src.providers.profiles import ModelProfile


class DeepSeekClient:
    """适配 DeepSeek 原生思考字段的 Chat Completions 客户端。"""

    profile: ModelProfile
    api_key: str
    base_url: str
    model: str

    def __init__(self, profile: ModelProfile):
        self.profile = profile
        self.api_key = profile.api_key
        self.base_url = profile.base_url.rstrip("/")
        self.model = profile.model

    def complete(self, messages: list[dict], *, model: str, max_tokens: int, temperature: float, model_profile: dict | None = None) -> ModelResponse:
        if not self.api_key:
            context = messages[-1].get("content", "") if messages else ""
            return ModelResponse(
                text="<final>JCode is configured without an API key. The context was built but no provider request was sent.</final>",
                input_tokens=estimate_tokens(context),
                output_tokens=30,
            )
        payload_data: dict[str, object] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        # 按 DeepSeek OpenAI 格式显式控制思考模式。
        options = dict(model_profile or self.profile.snapshot())
        thinking_enabled = bool(options.get("thinking_enabled", False))
        payload_data["thinking"] = {"type": "enabled" if thinking_enabled else "disabled"}
        if thinking_enabled:
            reasoning_effort = options.get("reasoning_effort")
            if reasoning_effort:
                payload_data["reasoning_effort"] = reasoning_effort
        else:
            payload_data["temperature"] = temperature
        # 允许各模型档案传递 DeepSeek 专有的其他请求参数，但不允许覆盖思考控制字段。
        payload_data.update({key: value for key, value in self.profile.extra.items() if key not in {"thinking", "reasoning_effort"}})
        payload = json.dumps(payload_data).encode("utf-8")
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
            raise RuntimeError(f"deepseek provider error {exc.code}: {body[:500]}") from exc
        message = data.get("choices", [{}])[0].get("message", {})
        usage = data.get("usage", {})
        return ModelResponse(
            text=str(message.get("content") or ""),
            reasoning=str(message.get("reasoning_content") or ""),
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            raw=data,
        )
