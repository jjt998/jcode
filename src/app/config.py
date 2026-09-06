from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from src.providers.profiles import ModelProfile
from src.context.budget import DEFAULT_MAX_NEW_TOKENS, SAFETY_MARGIN, TokenizerAdapter, effective_window, validate_static_model_capacity, serialize_counted_input


def validate_profile_capacity_fields(profile: ModelProfile) -> None:
    """验证模型档案声明的窗口和输出上限。"""
    for name in ("context_window_tokens", "max_output_tokens"):
        value = getattr(profile, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")


def resolve_actual_max_new_tokens(cli_value, toml_value, default: int = DEFAULT_MAX_NEW_TOKENS) -> int:
    """按 CLI > TOML > 默认值解析输出预留。"""
    value = cli_value if cli_value is not None else (toml_value if toml_value is not None else default)
    value = int(value)
    if value <= 0:
        raise ValueError("max_new_tokens must be positive")
    return value


def compile_static_capacity_input(prefix: str = "", tools: list[dict] | None = None, tokenizer: TokenizerAdapter | None = None) -> dict:
    """编译启动时已知的 instructions/tools，并加入三项最低保障。"""
    adapter = tokenizer or TokenizerAdapter()
    if prefix or tools:
        _, occupancy = serialize_counted_input(prefix, [], list(tools or []), adapter)
        details = {item.name: item.tokens for item in occupancy}
    else:
        details = {"instructions": 0, "tools_schema": 0, "provider_protocol_envelope": 0}
    details.update({"history": 8192, "working_memory": 4096, "skills": 512})
    details["total"] = sum(details.values())
    return details


@dataclass
class AppConfig:
    cwd: Path
    provider_name: str
    api_protocol: str
    model_profiles: dict[str, ModelProfile]
    default_model_profile: str
    approval: str
    sandbox: str
    max_steps: int
    max_new_tokens: int
    temperature: float
    explicit_model_profile: str | None = None  # CLI 显式指定的模型档案
    plan_topic: str | None = None
    plan_path: str | None = None
    auto_dream: bool = False
    dream_interval_hours: float = 24.0
    dream_min_sessions: int = 5
    session_id: str | None = None
    resume: str | None = None
    compact_summary_timeout_seconds: int = 120  # 摘要模型超时秒数
    compact_summary_retry_count: int = 2  # 摘要失败后的额外重试次数
    compact_summary_initial_retry_delay_seconds: int = 2  # 首次重试等待秒数
    compact_summary_retry_multiplier: int = 2  # 摘要重试等待倍数
    compact_summary_rebuild_on: bool = True  # 是否允许从历史 artifact 重建摘要


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


def _profile_from_raw(profile_id: str, raw: dict, *, providers: dict[str, dict], api_key_override: str | None, base_url_override: str | None) -> ModelProfile:
    """将模型档案和其引用的 Provider 配置转换为运行时配置。"""
    provider_id = str(raw.get("provider") or "").strip()
    provider = providers.get(provider_id)
    if provider is None:
        raise ValueError(f"model profile {profile_id} references unknown provider: {provider_id}")
    provider_name = str(provider.get("name") or "").strip()
    api_protocol = str(provider.get("api_protocol") or "").strip()
    model = str(raw.get("model") or "").strip()
    base_url = str(base_url_override or provider.get("base_url") or "").rstrip("/")
    api_key_env = str(provider.get("api_key_env") or "").strip()
    api_key = str(api_key_override or provider.get("api_key") or (os.environ.get(api_key_env) if api_key_env else "") or "")
    reasoning_mode = str(raw.get("reasoning_mode") or "none").strip()
    thinking_enabled = _as_bool(raw.get("thinking_enabled", False))
    reasoning_always_on = _as_bool(raw.get("reasoning_always_on", False))
    context_window_tokens = raw.get("context_window_tokens")
    max_output_tokens = raw.get("max_output_tokens")
    for field_name, value in (("context_window_tokens", context_window_tokens), ("max_output_tokens", max_output_tokens)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"model profile {profile_id} requires positive integer {field_name}")
    if not provider_name or not api_protocol or not model or not base_url:
        raise ValueError(f"model profile {profile_id} requires global provider and model")
    if reasoning_mode not in {"none", "native", "optional"}:
        raise ValueError(f"model profile {profile_id} has invalid reasoning_mode: {reasoning_mode}")
    if reasoning_mode == "none" and (thinking_enabled or reasoning_always_on):
        raise ValueError(f"model profile {profile_id} cannot enable thinking when reasoning_mode is none")
    reasoning_effort = str(raw.get("reasoning_effort") or "")
    effort_options = tuple(str(value) for value in raw.get("reasoning_effort_options", []))
    allowed_efforts = {"low", "medium", "high", "xhigh", "max"}
    if provider_name == "minimax":
        allowed_efforts = {"minimal", "low", "medium", "high"}
    if reasoning_effort and reasoning_effort not in allowed_efforts:
        raise ValueError(f"model profile {profile_id} has invalid reasoning_effort: {reasoning_effort}")
    if any(value not in allowed_efforts for value in effort_options):
        raise ValueError(f"model profile {profile_id} has invalid reasoning_effort_options")
    if reasoning_mode == "none" and (reasoning_effort or effort_options):
        raise ValueError(f"model profile {profile_id} cannot declare effort when reasoning_mode is none")
    if reasoning_mode != "none" and (not reasoning_effort or not effort_options or reasoning_effort not in effort_options):
        raise ValueError(f"model profile {profile_id} requires a default effort included in reasoning_effort_options")
    if reasoning_always_on and not thinking_enabled:
        thinking_enabled = True
    known = {"provider", "model", "reasoning_mode", "thinking_enabled", "reasoning_always_on", "reasoning_effort", "reasoning_effort_options", "context_window_tokens", "max_output_tokens"}
    return ModelProfile(
        id=profile_id,
        provider=provider_name,
        api_protocol=api_protocol,
        model=model,
        api_key=api_key,
        base_url=base_url,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        reasoning_mode=reasoning_mode,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
        reasoning_effort_options=effort_options,
        reasoning_always_on=reasoning_always_on,
        extra={key: value for key, value in raw.items() if key not in known},
    )


def load_config(args) -> AppConfig:
    cwd = Path(args.cwd).resolve()
    # --cwd 仅用于定位当前工作项目，模型配置始终来自 JCode 安装目录。
    config_path = Path(args.config).resolve() if getattr(args, "config", None) else global_config_path()
    raw = _load_toml(config_path)
    providers_raw = raw.get("providers", {})
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise ValueError("configuration requires at least one [providers.<provider_id>] section")
    providers: dict[str, dict] = {}
    for provider_id, provider_value in providers_raw.items():
        if not isinstance(provider_value, dict):
            raise ValueError("every provider must be a TOML table")
        provider = dict(provider_value)
        name = str(provider.get("name") or "").strip()
        protocol = str(provider.get("api_protocol") or "").strip()
        if name not in {"deepseek", "minimax"} or protocol != "openai_responses":
            raise ValueError(f"unsupported provider protocol: {name}/{protocol}")
        providers[str(provider_id)] = provider
    models_raw = raw.get("models", {})
    if not isinstance(models_raw, dict) or not models_raw:
        raise ValueError("configuration requires at least one [models.<profile_id>] section")
    cli_model = getattr(args, "model", None)
    configured_default = str(raw.get("default_model") or "").strip()
    selected = str(cli_model if cli_model is not None else configured_default).strip()
    if not selected:
        raise ValueError("configuration requires default_model")
    if selected not in models_raw:
        raise ValueError(f"unknown selected model profile: {selected}")
    profiles = {
        profile_id: _profile_from_raw(
            profile_id,
            dict(profile_raw),
            providers=providers,
            api_key_override=getattr(args, "api_key", None) if profile_id == selected else None,
            base_url_override=getattr(args, "base_url", None) if profile_id == selected else None,
        )
        for profile_id, profile_raw in models_raw.items()
        if isinstance(profile_raw, dict)
    }
    if len(profiles) != len(models_raw):
        raise ValueError("every model profile must be a TOML table")
    for profile in profiles.values():
        validate_profile_capacity_fields(profile)
    default_profile = profiles[selected]
    security_raw = dict(raw.get("security", {}))
    runtime_raw = dict(raw.get("runtime", {}))
    resolved_max_new_tokens = resolve_actual_max_new_tokens(getattr(args, "max_new_tokens", None), runtime_raw.get("max_new_tokens"), DEFAULT_MAX_NEW_TOKENS)
    if resolved_max_new_tokens > default_profile.max_output_tokens:
        raise ValueError(f"max_new_tokens exceeds model profile output limit: {resolved_max_new_tokens} > {default_profile.max_output_tokens}")
    static_minimum = compile_static_capacity_input()["total"]
    validate_static_model_capacity(default_profile, resolved_max_new_tokens, static_minimum)
    memory_raw = dict(raw.get("memory", {}))
    return AppConfig(
        cwd=cwd,
        provider_name=default_profile.provider,
        api_protocol=default_profile.api_protocol,
        model_profiles=profiles,
        default_model_profile=configured_default or selected,
        approval=str(getattr(args, "approval", None) or security_raw.get("approval") or "ask"),
        sandbox=str(getattr(args, "sandbox", None) or security_raw.get("sandbox") or "best_effort"),
        max_steps=int(getattr(args, "max_steps", None) if getattr(args, "max_steps", None) is not None else runtime_raw.get("max_steps", 50)),
        max_new_tokens=resolved_max_new_tokens,
        temperature=float(getattr(args, "temperature", None) or 0.2),
        plan_topic=str(getattr(args, "plan_topic", None) or runtime_raw.get("plan_topic") or "") or None,
        plan_path=str(getattr(args, "plan_path", None) or runtime_raw.get("plan_path") or "") or None,
        auto_dream=_as_bool(memory_raw.get("auto_dream", False)),
        dream_interval_hours=float(memory_raw.get("dream_interval_hours", 24.0)),
        dream_min_sessions=int(memory_raw.get("dream_min_sessions", 5)),
        session_id=getattr(args, "session_id", None),
        resume=getattr(args, "resume", None),
        explicit_model_profile=str(cli_model).strip() if cli_model is not None else None,
        compact_summary_timeout_seconds=int(runtime_raw.get("compact_summary_timeout_seconds", 120)),
        compact_summary_retry_count=int(runtime_raw.get("compact_summary_retry_count", 2)),
        compact_summary_initial_retry_delay_seconds=int(runtime_raw.get("compact_summary_initial_retry_delay_seconds", 2)),
        compact_summary_retry_multiplier=int(runtime_raw.get("compact_summary_retry_multiplier", 2)),
        compact_summary_rebuild_on=_as_bool(runtime_raw.get("compact_summary_rebuild_on", True)),
    )
