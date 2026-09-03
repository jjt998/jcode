from __future__ import annotations

from src.providers.profiles import ModelProfile
from src.state.model_selection import resolve_model_snapshot, validate_model_options


def test_session_reasoning_effort_overrides_profile_default():
    profile = ModelProfile("deepseek", "deepseek", "deepseek-v4-pro", "", "https://api.deepseek.com", reasoning_mode="optional", thinking_enabled=True, reasoning_effort="high", reasoning_effort_options=("low", "high", "max"))

    snapshot = resolve_model_snapshot({"model_options": {"deepseek": {"thinking_enabled": True, "reasoning_effort": "max"}}}, profile)

    assert snapshot["reasoning_effort"] == "max"
    assert snapshot["thinking_enabled"] is True


def test_reasoning_options_reject_effort_not_declared_by_profile():
    profile = ModelProfile("deepseek", "deepseek", "deepseek-v4-pro", "", "https://api.deepseek.com", reasoning_mode="optional", reasoning_effort="high", reasoning_effort_options=("low", "high"))

    try:
        validate_model_options(profile, True, "max")
    except ValueError as exc:
        assert "reasoning_effort" in str(exc)
    else:
        raise AssertionError("expected invalid effort to be rejected")
