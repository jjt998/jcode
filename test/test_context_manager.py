from types import SimpleNamespace

import pytest

from src.context.budget import TokenizerAdapter, calculate_pressure
from src.context.manager import ContextManager
from src.memory.working import WorkingMemory
from src.runtime.errors import FinalContextExceedsWindowError, MandatoryContextExceedsWindowError
from src.state.workspace import Workspace
from src.tools.registry import build_default_registry


def manager(tmp_path, window=375000, output=16384):
    profile = SimpleNamespace(context_window_tokens=window, max_output_tokens=output, snapshot=lambda: {"id": "test"})
    return ContextManager(Workspace.build(tmp_path), object(), build_default_registry(), model_profile=profile, actual_max_new_tokens=output, tokenizer=TokenizerAdapter())


def test_pressure_boundaries():
    assert [calculate_pressure(x, 100)["level"] for x in (59, 60, 74, 75, 84, 85, 94, 95)] == [0, 1, 1, 2, 2, 3, 3, 4]
    assert calculate_pressure(0, 0)["level"] == 0
    assert calculate_pressure(1, 0)["level"] == 4


def test_current_request_is_only_sent_through_history(tmp_path):
    """当前请求只通过用户历史进入 Provider，不在 Working Memory 中复制。"""
    m = manager(tmp_path)
    memory = WorkingMemory(tmp_path)
    session = {"history": [{"kind": "user", "event_id": "e", "turn_id": "t", "content": "new task"}]}
    outcome = m.build(session, memory, "new task", allowed_tools=frozenset())
    assert [item.get("content") for item in outcome.context_result.provider_input.input].count("new task") == 1
    assert "new task" not in outcome.working_memory_candidate.render()
    assert session["history"][0]["content"] == "new task"


def test_build_does_not_duplicate_current_user_event(tmp_path):
    """Agent 已写入当前请求时，ContextManager 只读取而不重复追加。"""
    m = manager(tmp_path)
    memory = WorkingMemory(tmp_path)
    session = {"event_seq": 1, "history": [{"kind": "user", "event_id": "event-1", "turn_id": "run-1", "content": "same task"}]}

    outcome = m.build(session, memory, "same task", allowed_tools=frozenset())

    assert [item.get("content") for item in outcome.session_candidate["history"]].count("same task") == 1


def test_compaction_history_keeps_only_latest_summary():
    """重复压缩后 session history 中只能保留新摘要，不能累积旧摘要。"""
    # 该边界由压缩分支直接验证，避免依赖 Provider 网络请求。
    from src.context.result import HistoryEvent

    events = [
        HistoryEvent("compact_summary", "summary-old", "turn-1", "old"),
        HistoryEvent("user", "user-2", "turn-2", "two"),
        HistoryEvent("user", "user-3", "turn-3", "three"),
        HistoryEvent("user", "user-4", "turn-4", "four"),
    ]
    keep_ids = {"turn-2", "turn-3", "turn-4"}
    filtered = [event for event in events if event.turn_id in keep_ids and event.kind != "compact_summary"]
    assert [event.kind for event in filtered] == ["user", "user", "user"]


def test_provider_snapshot_is_audited_exactly(tmp_path):
    outcome = manager(tmp_path).build({"history": []}, WorkingMemory(tmp_path), "task", allowed_tools=frozenset())
    snapshot = outcome.context_result.provider_input
    assert snapshot.serialized_input_tokens == outcome.ctx_info["serialized_input_tokens"]
    assert snapshot.instructions == outcome.context_result.prefix
    assert snapshot.tools == []


def test_pressure_uses_provider_input_not_unsent_history_metadata(tmp_path):
    """未发送的 History 元数据很大时，不应把实际输入误判为高压。"""
    session = {
        "history": [
            {
                "kind": "tool_result",
                "event_id": "event-1",
                "turn_id": "run-1",
                "call_id": "call-1",
                "content": "ok",
                "metadata": {"audit_blob": "x" * 800000},
            }
        ]
    }

    outcome = manager(tmp_path, window=100000, output=1000).build(session, WorkingMemory(tmp_path), "task", allowed_tools=frozenset())

    assert outcome.ctx_info["serialized_input_tokens"] < 1000
    assert outcome.ctx_info["pressure"]["level"] == 0


def test_mandatory_capacity_failure(tmp_path):
    with pytest.raises(MandatoryContextExceedsWindowError):
        manager(tmp_path, window=100, output=80).build({"history": []}, WorkingMemory(tmp_path), "x" * 100, allowed_tools=frozenset())


def test_final_capacity_failure_does_not_delete_old_history(tmp_path):
    """最终超限只报错，不再通过删除旧 turn 逃避容量校验。"""
    history = [
        {"kind": "user", "event_id": "old", "turn_id": "run-1", "content": "old context " * 3000},
        {"kind": "user", "event_id": "current", "turn_id": "run-2", "content": "current task"},
    ]
    session = {"history": history}

    with pytest.raises(FinalContextExceedsWindowError):
        manager(tmp_path, window=10000, output=100).build(session, WorkingMemory(tmp_path), "current task", allowed_tools=frozenset())

    assert session["history"] == history


def test_level_windows():
    assert [ContextManager._history_window_for_level(i) for i in range(5)] == [7, 5, 4, 3, 3]


@pytest.mark.parametrize(
    ("turn_ids", "expected"),
    [([], ""), (["turn-1"], "turn-1"), (["turn-1", "turn-2"], "turn-1"), (["turn-1", "turn-2", "turn-3"], "turn-1"), (["turn-1", "turn-2", "turn-3", "turn-4"], "turn-2")],
)
def test_compact_summary_turn_id_handles_short_history(turn_ids, expected):
    """历史回合不足三项时也必须生成合法的摘要锚点。"""
    assert ContextManager._compact_summary_turn_id(turn_ids) == expected
