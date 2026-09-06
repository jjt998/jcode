from __future__ import annotations

from argparse import Namespace

from src.app import config as app_config


def _args(cwd: str) -> Namespace:
    return Namespace(
        cwd=cwd,
        config=None,
        model=None,
        api_key=None,
        base_url=None,
        approval=None,
        sandbox=None,
        max_steps=None,
        max_new_tokens=None,
        temperature=0.2,
        plan_topic=None,
        plan_path=None,
        session_id=None,
        resume=None,
    )


def _add_capacity(text: str) -> str:
    return text.replace('model = "deepseek-v4-flash"', 'model = "deepseek-v4-flash"\ncontext_window_tokens = 1048576\nmax_output_tokens = 393216').replace('model = "MiniMax-M3"', 'model = "MiniMax-M3"\ncontext_window_tokens = 1000000\nmax_output_tokens = 524288').replace('model = "deepseek-v4-pro"', 'model = "deepseek-v4-pro"\ncontext_window_tokens = 1048576\nmax_output_tokens = 393216')


def test_default_config_is_global_and_cwd_remains_project(tmp_path, monkeypatch):
    global_root = tmp_path / "jcode"
    global_root.mkdir()
    global_config = global_root / ".jcode.toml"
    global_config.write_text(
        _add_capacity("""
default_model = "global-model"

[providers.deepseek]
name = "deepseek"
api_protocol = "openai_responses"
base_url = "https://api.deepseek.com"
api_key = "global-key"

[models.global-model]
provider = "deepseek"
model = "deepseek-v4-flash"
reasoning_mode = "native"
thinking_enabled = true
reasoning_effort = "high"
reasoning_effort_options = ["low", "high", "max"]
    """.strip()),
        encoding="utf-8",
    )
    project_root = tmp_path / "demo1"
    project_root.mkdir()
    (project_root / ".jcode.toml").write_text("default_model = \"project-model\"", encoding="utf-8")
    monkeypatch.setattr(app_config, "global_config_path", lambda: global_config)

    config = app_config.load_config(_args(str(project_root)))

    assert config.cwd == project_root.resolve()
    assert config.default_model_profile == "global-model"
    assert config.model_profiles["global-model"].api_key == "global-key"


def test_minimax_config_accepts_minimal_reasoning_effort(tmp_path, monkeypatch):
    config_path = tmp_path / ".jcode.toml"
    config_path.write_text(
        _add_capacity("""
default_model = "minimax-m3"
[providers.minimax]
name = "minimax"
api_protocol = "openai_responses"
base_url = "https://minnimax.chat/v1"
[models.minimax-m3]
provider = "minimax"
model = "MiniMax-M3"
reasoning_mode = "optional"
thinking_enabled = false
reasoning_effort = "minimal"
reasoning_effort_options = ["minimal", "low", "medium", "high"]
    """.strip()),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_config, "global_config_path", lambda: config_path)

    config = app_config.load_config(_args(str(tmp_path)))

    assert config.provider_name == "minimax"
    assert config.model_profiles["minimax-m3"].reasoning_effort == "minimal"


def test_multiple_providers_are_resolved_per_model_profile(tmp_path, monkeypatch):
    config_path = tmp_path / ".jcode.toml"
    config_path.write_text(
        _add_capacity("""
default_model = "minimax-m3"
[providers.deepseek]
name = "deepseek"
api_protocol = "openai_responses"
base_url = "https://api.deepseek.com"
api_key = "deepseek-key"
[providers.minimax]
name = "minimax"
api_protocol = "openai_responses"
base_url = "https://minnimax.chat/v1"
api_key = "minimax-key"
[models.deepseek]
provider = "deepseek"
model = "deepseek-v4-pro"
reasoning_mode = "native"
thinking_enabled = true
reasoning_effort = "high"
reasoning_effort_options = ["low", "high", "max"]
[models.minimax-m3]
provider = "minimax"
model = "MiniMax-M3"
reasoning_mode = "optional"
thinking_enabled = false
reasoning_effort = "minimal"
reasoning_effort_options = ["minimal", "low", "medium", "high"]
    """.strip()),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_config, "global_config_path", lambda: config_path)

    config = app_config.load_config(_args(str(tmp_path)))

    assert config.model_profiles["deepseek"].provider == "deepseek"
    assert config.model_profiles["deepseek"].base_url == "https://api.deepseek.com"
    assert config.model_profiles["minimax-m3"].provider == "minimax"
    assert config.model_profiles["minimax-m3"].base_url == "https://minnimax.chat/v1"
