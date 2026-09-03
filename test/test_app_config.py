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


def test_default_config_is_global_and_cwd_remains_project(tmp_path, monkeypatch):
    global_root = tmp_path / "jcode"
    global_root.mkdir()
    global_config = global_root / ".jcode.toml"
    global_config.write_text(
        """
default_model = "global-model"

[models.global-model]
provider = "deepseek"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com"
api_key = "global-key"
reasoning_mode = "native"
thinking_enabled = true
reasoning_effort = "high"
reasoning_effort_options = ["low", "high", "max"]
""".strip(),
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
