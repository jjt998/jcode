from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from src.providers.profiles import ModelProfile


@dataclass
class AppConfig:
    cwd: Path
    model_profiles: dict[str, ModelProfile]
    default_model_profile: str
    approval: str
    sandbox: str
    max_steps: int
    max_new_tokens: int
    temperature: float
    plan_topic: str | None = None
    plan_path: str | None = None
    auto_dream: bool = False
    dream_interval_hours: float = 24.0
    dream_min_sessions: int = 5
    session_id: str | None = None
    resume: str | None = None


def _load_toml(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def global_config_path() -> Path:
    """返回 JCode 安装目录中的全局 Provider 配置文件。"""
    return Path(__file__).resolve().parents[2] / ".jcode.toml"


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _profile_from_raw(profile_id: str, raw: dict, *, api_key_override: str | None, base_url_override: str | None) -> ModelProfile:
    """将 TOML 模型档案转换为运行时配置。"""
    provider = str(raw.get("provider") or "").strip()
    model = str(raw.get("model") or "").strip()
    base_url = str(base_url_override or raw.get("base_url") or "").rstrip("/")
    api_key_env = str(raw.get("api_key_env") or "").strip()
    api_key = str(api_key_override or raw.get("api_key") or (os.environ.get(api_key_env) if api_key_env else "") or "")
    reasoning_mode = str(raw.get("reasoning_mode") or "none").strip()
    thinking_enabled = _as_bool(raw.get("thinking_enabled", False))
    if not provider or not model or not base_url:
        raise ValueError(f"model profile {profile_id} requires provider, model, and base_url")
    if reasoning_mode not in {"none", "native", "optional"}:
        raise ValueError(f"model profile {profile_id} has invalid reasoning_mode: {reasoning_mode}")
    if reasoning_mode == "none" and thinking_enabled:
        raise ValueError(f"model profile {profile_id} cannot enable thinking when reasoning_mode is none")
    reasoning_effort = str(raw.get("reasoning_effort") or "")
    effort_options = tuple(str(value) for value in raw.get("reasoning_effort_options", []))
    allowed_efforts = {"low", "medium", "high", "xhigh", "max"}
    if reasoning_effort and reasoning_effort not in allowed_efforts:
        raise ValueError(f"model profile {profile_id} has invalid reasoning_effort: {reasoning_effort}")
    if any(value not in allowed_efforts for value in effort_options):
        raise ValueError(f"model profile {profile_id} has invalid reasoning_effort_options")
    if reasoning_mode == "none" and (reasoning_effort or effort_options):
        raise ValueError(f"model profile {profile_id} cannot declare effort when reasoning_mode is none")
    if reasoning_mode != "none" and (not reasoning_effort or not effort_options or reasoning_effort not in effort_options):
        raise ValueError(f"model profile {profile_id} requires a default effort included in reasoning_effort_options")
    known = {"provider", "model", "api_key", "api_key_env", "base_url", "reasoning_mode", "thinking_enabled", "reasoning_effort", "reasoning_effort_options"}
    return ModelProfile(
        id=profile_id,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        reasoning_mode=reasoning_mode,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
        reasoning_effort_options=effort_options,
        extra={key: value for key, value in raw.items() if key not in known},
    )


def load_config(args) -> AppConfig:
    cwd = Path(args.cwd).resolve()
    # --cwd 仅用于定位当前工作项目，模型配置始终来自 JCode 安装目录。
    config_path = Path(args.config).resolve() if getattr(args, "config", None) else global_config_path()
    raw = _load_toml(config_path)
    models_raw = raw.get("models", {})
    if not isinstance(models_raw, dict) or not models_raw:
        raise ValueError("configuration requires at least one [models.<profile_id>] section")
    selected = str(getattr(args, "model", None) or raw.get("default_model") or "").strip()
    if not selected:
        raise ValueError("configuration requires default_model")
    if selected not in models_raw:
        raise ValueError(f"unknown selected model profile: {selected}")
    profiles = {
        profile_id: _profile_from_raw(
            profile_id,
            dict(profile_raw),
            api_key_override=getattr(args, "api_key", None) if profile_id == selected else None,
            base_url_override=getattr(args, "base_url", None) if profile_id == selected else None,
        )
        for profile_id, profile_raw in models_raw.items()
        if isinstance(profile_raw, dict)
    }
    if len(profiles) != len(models_raw):
        raise ValueError("every model profile must be a TOML table")
    security_raw = dict(raw.get("security", {}))
    runtime_raw = dict(raw.get("runtime", {}))
    memory_raw = dict(raw.get("memory", {}))
    return AppConfig(
        cwd=cwd,
        model_profiles=profiles,
        default_model_profile=selected,
        approval=str(getattr(args, "approval", None) or security_raw.get("approval") or "ask"),
        sandbox=str(getattr(args, "sandbox", None) or security_raw.get("sandbox") or "best_effort"),
        max_steps=int(getattr(args, "max_steps", None) or runtime_raw.get("max_steps") or 50),
        max_new_tokens=int(getattr(args, "max_new_tokens", None) or runtime_raw.get("max_new_tokens") or 8192),
        temperature=float(getattr(args, "temperature", 0.2)),
        plan_topic=str(getattr(args, "plan_topic", None) or runtime_raw.get("plan_topic") or "") or None,
        plan_path=str(getattr(args, "plan_path", None) or runtime_raw.get("plan_path") or "") or None,
        auto_dream=_as_bool(memory_raw.get("auto_dream", False)),
        dream_interval_hours=float(memory_raw.get("dream_interval_hours", 24.0)),
        dream_min_sessions=int(memory_raw.get("dream_min_sessions", 5)),
        session_id=getattr(args, "session_id", None),
        resume=getattr(args, "resume", None),
    )
