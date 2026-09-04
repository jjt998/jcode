from __future__ import annotations

from src.providers.profiles import ModelProfile
from src.state.model_selection import resolve_model_snapshot, validate_model_options


def test_session_reasoning_effort_overrides_profile_default():
    profile = ModelProfile("deepseek", "deepseek", "openai_responses", "deepseek-v4-pro", "", "https://api.deepseek.com", reasoning_mode="optional", thinking_enabled=True, reasoning_effort="high", reasoning_effort_options=("low", "high", "max"))

    snapshot = resolve_model_snapshot({"model_options": {"deepseek": {"thinking_enabled": True, "reasoning_effort": "max"}}}, profile)

    assert snapshot["reasoning_effort"] == "max"
    assert snapshot["thinking_enabled"] is True


def test_reasoning_options_reject_effort_not_declared_by_profile():
    profile = ModelProfile("deepseek", "deepseek", "openai_responses", "deepseek-v4-pro", "", "https://api.deepseek.com", reasoning_mode="optional", reasoning_effort="high", reasoning_effort_options=("low", "high"))

    try:
        validate_model_options(profile, True, "max")
    except ValueError as exc:
        assert "reasoning_effort" in str(exc)
    else:
        raise AssertionError("expected invalid effort to be rejected")


def test_reasoning_toggle_is_persisted_in_model_snapshot():
    profile = ModelProfile("minimax", "minimax", "openai_responses", "MiniMax-M3", "", "https://minnimax.chat/v1", reasoning_mode="optional", thinking_enabled=False, reasoning_effort="medium", reasoning_effort_options=("minimal", "low", "medium", "high"))

    snapshot = resolve_model_snapshot({"model_options": {"minimax": {"thinking_enabled": True, "reasoning_effort": "minimal"}}}, profile)

    assert snapshot["thinking_enabled"] is True
    assert snapshot["reasoning_effort"] == "minimal"


def test_always_on_reasoning_cannot_be_disabled():
    profile = ModelProfile("minimax-m2", "minimax", "openai_responses", "MiniMax-M2", "", "https://minnimax.chat/v1", reasoning_mode="native", thinking_enabled=True, reasoning_effort="medium", reasoning_effort_options=("minimal", "low", "medium", "high"), reasoning_always_on=True)

    options = validate_model_options(profile, False, "low")
    snapshot = resolve_model_snapshot({"model_options": {"minimax-m2": {"thinking_enabled": False, "reasoning_effort": "low"}}}, profile)

    assert options["thinking_enabled"] is True
    assert snapshot["thinking_enabled"] is True
