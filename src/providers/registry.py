from __future__ import annotations

from typing import Callable

from src.providers.base import ModelClient
from src.providers.profiles import ModelProfile


class ModelRegistry:
    """集中管理模型档案和对应的 provider 客户端。"""

    profiles: dict[str, ModelProfile]
    default_profile_id: str
    _factories: dict[tuple[str, str], Callable[[ModelProfile], ModelClient]]
    _clients: dict[str, ModelClient]

    def __init__(self, profiles: dict[str, ModelProfile], default_profile_id: str):
        if default_profile_id not in profiles:
            raise ValueError(f"unknown default model profile: {default_profile_id}")
        self.profiles = dict(profiles)
        self.default_profile_id = default_profile_id
        self._factories = {}
        self._clients = {}

    def register(self, provider: str, api_protocol: str, factory: Callable[[ModelProfile], ModelClient]) -> None:
        self._factories[(provider, api_protocol)] = factory

    def profile(self, profile_id: str | None = None) -> ModelProfile:
        selected = profile_id or self.default_profile_id
        try:
            return self.profiles[selected]
        except KeyError as exc:
            raise ValueError(f"unknown model profile: {selected}") from exc

    def client(self, profile_id: str | None = None) -> ModelClient:
        profile = self.profile(profile_id)
        if profile.id not in self._clients:
            try:
                factory = self._factories[(profile.provider, profile.api_protocol)]
            except KeyError as exc:
                raise ValueError(f"unregistered provider protocol: {profile.provider}/{profile.api_protocol}") from exc
            self._clients[profile.id] = factory(profile)
        return self._clients[profile.id]

    def list_profiles(self) -> list[dict]:
        return [self.profiles[key].snapshot() for key in sorted(self.profiles)]
