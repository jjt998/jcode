from __future__ import annotations

from src.providers.base import ModelResponse
from src.context.result import ContextResult
from src.providers.registry import ModelRegistry


class ModelRouter:
    registry: ModelRegistry

    def __init__(self, registry: ModelRegistry):
        self.registry = registry

    def complete(self, context: ContextResult, *, max_tokens: int, temperature: float, profile_id: str | None = None, model_profile: dict | None = None) -> ModelResponse:
        profile = self.registry.profile(profile_id)
        client = self.registry.client(profile.id)
        return client.complete(
            context,
            model=profile.model,
            max_tokens=max_tokens,
            temperature=temperature,
            model_profile=model_profile,
        )

    def request_preview(self, context: ContextResult, *, max_tokens: int, temperature: float, profile_id: str | None = None, model_profile: dict | None = None) -> dict:
        """委托当前 Provider 生成与实际发送一致的脱敏请求预览。"""
        profile = self.registry.profile(profile_id)
        client = self.registry.client(profile.id)
        return client.request_preview(
            context,
            model=profile.model,
            max_tokens=max_tokens,
            temperature=temperature,
            model_profile=model_profile,
        )

    def has_api_key(self, profile_id: str | None = None) -> bool:
        return bool(getattr(self.registry.client(profile_id), "api_key", ""))
