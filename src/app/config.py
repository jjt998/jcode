from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5"


@dataclass
class AppConfig:
    cwd: Path
    provider: str
    api_key: str
    base_url: str
    model: str
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


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_config(args) -> AppConfig:
    cwd = Path(args.cwd).resolve()
    config_path = Path(args.config).resolve() if args.config else cwd / ".jcode.toml"
    raw = _load_toml(config_path)
    provider = str(args.provider or raw.get("provider") or "openai")
    provider_raw = dict(raw.get("providers", {}).get(provider, {}))
    security_raw = dict(raw.get("security", {}))
    runtime_raw = dict(raw.get("runtime", {}))
    memory_raw = dict(raw.get("memory", {}))
    return AppConfig(
        cwd=cwd,
        provider=provider,
        api_key=str(args.api_key or os.environ.get("JCODE_API_KEY") or provider_raw.get("api_key") or ""),
        base_url=str(args.base_url or os.environ.get("JCODE_BASE_URL") or provider_raw.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
        model=str(args.model or os.environ.get("JCODE_MODEL") or provider_raw.get("model") or DEFAULT_MODEL),
        approval=str(args.approval or security_raw.get("approval") or "ask"),
        sandbox=str(args.sandbox or security_raw.get("sandbox") or "best_effort"),
        max_steps=int(args.max_steps or runtime_raw.get("max_steps") or 50),
        max_new_tokens=int(args.max_new_tokens or runtime_raw.get("max_new_tokens") or 8192),
        temperature=float(args.temperature),
        plan_topic=str(getattr(args, "plan_topic", None) or runtime_raw.get("plan_topic") or "") or None,
        plan_path=str(getattr(args, "plan_path", None) or runtime_raw.get("plan_path") or "") or None,
        auto_dream=_as_bool(memory_raw.get("auto_dream", False)),
        dream_interval_hours=float(memory_raw.get("dream_interval_hours", 24.0)),
        dream_min_sessions=int(memory_raw.get("dream_min_sessions", 5)),
        session_id=args.session_id,
        resume=args.resume,
    )
