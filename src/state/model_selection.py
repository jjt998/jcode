from __future__ import annotations

from src.providers.profiles import ModelProfile


def resolve_model_snapshot(session: dict, profile: ModelProfile) -> dict:
    """合并模型档案默认值与当前 session 的选择。"""
    snapshot = profile.snapshot()
    if profile.reasoning_mode == "none":
        return snapshot
    selected = dict(session.get("model_options", {}).get(profile.id, {}) or {})
    thinking_enabled = bool(selected.get("thinking_enabled", profile.thinking_enabled))
    effort = str(selected.get("reasoning_effort", profile.reasoning_effort) or "")
    if effort not in profile.reasoning_effort_options:
        effort = profile.reasoning_effort
    snapshot["thinking_enabled"] = thinking_enabled
    snapshot["reasoning_effort"] = effort
    return snapshot


def validate_model_options(profile: ModelProfile, thinking_enabled: bool, reasoning_effort: str) -> dict:
    """校验 Web 保存的思考设置，并返回规范化数据。"""
    if profile.reasoning_mode == "none":
        if thinking_enabled or reasoning_effort:
            raise ValueError("selected model does not support reasoning")
        return {}
    effort = str(reasoning_effort or profile.reasoning_effort)
    if effort not in profile.reasoning_effort_options:
        raise ValueError(f"reasoning_effort must be one of: {', '.join(profile.reasoning_effort_options)}")
    return {"thinking_enabled": bool(thinking_enabled), "reasoning_effort": effort}
