from __future__ import annotations

import threading
import time
from pathlib import Path

from src.app.config import AppConfig
from src.app.web_runs import WebRun, WebRunManager
from src.app.web_steps import build_reasoning_steps
from src.app.web_turns import build_session_turns
from src.providers.profiles import ModelProfile


def test_model_configuration_exposes_global_defaults_for_empty_session_area(tmp_path):
    """没有 session 时，前端也应取得全局默认模型及推理选项。"""
    manager = WebRunManager.__new__(WebRunManager)
    profile = ModelProfile(
        "minimax-m3",
        "minimax",
        "openai_responses",
        "MiniMax-M3",
        "",
        "https://example.test/v1",
        1_000_000,
        524_288,
        reasoning_mode="optional",
        thinking_enabled=True,
        reasoning_effort="high",
        reasoning_effort_options=("minimal", "low", "medium", "high"),
    )
    manager.config = AppConfig(
        cwd=tmp_path,
        provider_name="minimax",
        api_protocol="openai_responses",
        model_profiles={profile.id: profile},
        default_model_profile=profile.id,
        approval="never",
        sandbox="workspace",
        max_steps=1,
        max_new_tokens=1,
        temperature=0.0,
    )

    configuration = manager.model_configuration()

    assert configuration["default_model_profile"] == "minimax-m3"
    assert configuration["model_profiles"][0]["model"] == "MiniMax-M3"
    assert configuration["model_profiles"][0]["thinking_enabled"] is True
    assert configuration["model_profiles"][0]["reasoning_effort"] == "high"


def test_web_session_config_does_not_mark_new_session_as_resume(tmp_path):
    """新会话的 Web Agent 不应因配置 resume 而注入恢复上下文。"""
    manager = WebRunManager.__new__(WebRunManager)
    manager.config = AppConfig(
        cwd=tmp_path,
        provider_name="test",
        api_protocol="test",
        model_profiles={},
        default_model_profile="default",
        approval="never",
        sandbox="workspace",
        max_steps=1,
        max_new_tokens=1,
        temperature=0.0,
    )
    manager._session_model_profile = lambda project, session_id: "default"
    project = type("Project", (), {"root": tmp_path})()

    config = manager._config_for_session(project, "new-session")

    assert config.session_id == "new-session"
    assert config.resume is None


def test_steps_show_process_content_without_reasoning():
    events = [
        {"event": "context_built", "created_at": "2026-08-30T15:23:40Z", "context_audit_ref": ".jcode/runs/run-1/artifacts/context-0001.json"},
        {"event": "model_responded", "created_at": "2026-08-30T15:23:41Z", "response_text": "I will inspect the file.", "reasoning_text": "inspect", "native_tool_calls": [{"call_id": "call-1", "name": "read_file", "arguments": {"path": "README.md"}}]},
        {"event": "tool_requested", "created_at": "2026-08-30T15:23:42Z", "call_id": "call-1", "name": "read_file", "args": {"path": "README.md"}},
        {"event": "tool_executed", "created_at": "2026-08-30T15:23:43Z", "call_id": "call-1", "name": "read_file", "status": "success", "result_summary": "read ok", "artifact_ref": ".jcode/runs/run-1/artifacts/read_file-output.txt"},
        {"event": "model_responded", "created_at": "2026-08-30T15:23:44Z", "response_text": "done", "reasoning_text": "finish", "native_tool_calls": []},
        {"event": "web_run_completed", "created_at": "2026-08-30T15:23:45Z", "final_text": "done"},
    ]

    steps, final_text = build_reasoning_steps(events, run_id="run-1")

    assert final_text == "done"
    assert len(steps) == 2
    assert steps[0]["tool_calls"][0]["name"] == "read_file"
    assert steps[0]["tool_calls"][0]["result_text"] == "read ok"
    assert steps[0]["tool_calls"][0]["artifact_ref"].endswith("read_file-output.txt")
    assert steps[0]["process_content"] == "I will inspect the file."
    assert all(detail["event"] != "native_tool_calls_received" for detail in steps[0]["details"])
    assert steps[0]["context_audit_ref"].endswith("context-0001.json")
    assert "reasoning_text" not in steps[0]
    assert steps[1]["response_text"] == "done"
    assert steps[1]["process_content"] == ""


def test_steps_keep_full_tool_result_summary():
    full_result = "x" * 1601
    events = [
        {"event": "model_responded", "created_at": "2026-08-30T15:23:41Z", "native_tool_calls": [{"call_id": "call-1", "name": "read_file", "arguments": {}}]},
        {"event": "tool_requested", "created_at": "2026-08-30T15:23:42Z", "call_id": "call-1", "name": "read_file", "args": {}},
        {"event": "tool_executed", "created_at": "2026-08-30T15:23:43Z", "call_id": "call-1", "name": "read_file", "status": "success", "result_summary": full_result},
    ]

    steps, _ = build_reasoning_steps(events, run_id="run-1")

    assert steps[0]["tool_calls"][0]["result_text"] == full_result


def test_steps_preserve_compression_summary_and_attach_one_comparison():
    """压缩事件挂到后续模型步骤，重复事件不生成额外步骤或卡片。"""
    comparison = {
        "before": {"pressure_level": 4, "total_input_tokens": 128000},
        "after": {"pressure_level": 4, "total_input_tokens": 84000},
        "delta": {"fixed_items": {"history": -42000}, "total_released_tokens": 44000},
        "content_changes": [{"change": "removed"}],
        "result": {"status": "applied", "summary_text": "完整摘要\n第二行"},
    }
    events = [
        {"event": "context_compression_compared", "created_at": "2026-08-30T15:23:40Z", **comparison},
        {"event": "context_compression_compared", "created_at": "2026-08-30T15:23:40.100Z", **comparison},
        {"event": "model_responded", "created_at": "2026-08-30T15:23:41Z", "response_text": "完成", "native_tool_calls": []},
    ]

    steps, _ = build_reasoning_steps(events, run_id="run-1")

    assert len(steps) == 1
    assert steps[0]["compression_comparison"]["result"]["summary_text"] == "完整摘要\n第二行"
    assert steps[0]["compression_comparison"]["content_changes"] == [{"change": "removed"}]


def test_active_run_snapshot_contains_current_steps_and_cursor():
    run = WebRun("web-run-1", "project-1", Path("."), "session-1", user_message="检查代码")
    run.reasoning_steps = [{"step_id": "run-1:1", "index": 1, "status": "running"}]
    run.emit("step_patch", step=run.reasoning_steps[0])

    snapshot = run.snapshot()

    assert snapshot["user_message"] == "检查代码"
    assert snapshot["reasoning_steps"] == [{"step_id": "run-1:1", "index": 1, "status": "running"}]
    assert snapshot["event_cursor"] == 1


def test_run_lookup_rejects_mismatched_project_or_session():
    manager = WebRunManager.__new__(WebRunManager)
    manager.lock = threading.RLock()
    run = WebRun("web-run-1", "project-1", Path("."), "session-1")
    manager.runs = {run.web_run_id: run}

    assert manager.get_run("web-run-1", project_id="project-1", session_id="session-1") is run

    try:
        manager.get_run("web-run-1", project_id="project-2")
    except KeyError:
        pass
    else:
        raise AssertionError("run lookup must reject another project")

    try:
        manager.get_run("web-run-1", session_id="session-2")
    except KeyError:
        pass
    else:
        raise AssertionError("run lookup must reject another session")


def test_abort_returns_aborting_without_waiting_for_run_thread():
    """abort 接口只登记请求，不能阻塞等待后台工具线程结束。"""
    manager = WebRunManager.__new__(WebRunManager)
    manager.lock = threading.RLock()
    run = WebRun("web-run-1", "project-1", Path("."), "session-1")

    class Agent:
        def __init__(self):
            self.aborted = False

        def abort(self):
            self.aborted = True

    agent = Agent()
    run.agent = agent
    run.status = "running"
    run.thread = threading.Thread(target=lambda: time.sleep(0.5), daemon=True)
    run.thread.start()
    manager.runs = {run.web_run_id: run}

    started = time.monotonic()
    result = manager.abort(run.web_run_id, timeout_seconds=10, project_id="project-1", session_id="session-1")

    assert time.monotonic() - started < 0.5
    assert result.status == "aborting"
    assert agent.aborted is True
    assert run.events[-1]["event"] == "run_abort_requested"


def test_session_turns_keep_history_order_when_run_ids_are_reversed():
    """会话刷新必须按消息原始顺序显示，不能按 run_ids 的偶然顺序重排。"""
    session = {
        "id": "session-1",
        "run_ids": ["run-later", "run-first"],
        "history": [
            {"kind": "user", "content": "第一轮", "run_id": "run-first"},
            {"kind": "assistant", "content": "第一轮完成", "run_id": "run-first"},
            {"kind": "user", "content": "未绑定运行"},
            {"kind": "user", "content": "第二轮", "run_id": "run-later"},
        ],
    }

    # 本用例不读取运行产物，传入不存在的路径即可避免依赖系统临时目录权限。
    turns = build_session_turns("project-1", Path("history-order-root"), session)["turns"]

    assert [turn["run_id"] for turn in turns] == ["run-first", "history-2", "run-later"]
    assert [turn["sequence"] for turn in turns] == [0, 1, 2]


def test_session_turns_attach_turn_id_and_current_messages_to_their_run():
    """中途确认消息的 turn_id 不能被错误展示为独立的空轮次。"""
    session = {
        "id": "session-1",
        "run_ids": ["run-1"],
        "history": [
            {"kind": "user", "content": "开始任务", "turn_id": "run-1"},
            {"kind": "assistant", "content": "处理中", "turn_id": "run-1"},
            {"kind": "user", "content": "继续", "turn_id": "current"},
            {"kind": "assistant", "content": "已完成", "run_id": "run-1", "turn_id": "run-1"},
        ],
    }

    turns = build_session_turns("project-1", Path("turn-id-order-root"), session)["turns"]

    assert [turn["run_id"] for turn in turns] == ["run-1"]
    assert turns[0]["user_message"] == "开始任务"
    assert turns[0]["final_text"] == "已完成"
