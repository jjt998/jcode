from types import SimpleNamespace

import pytest

from src.context.budget import TokenizerAdapter, calculate_pressure
from src.context.manager import ContextManager
from src.memory.working import WorkingMemory
from src.runtime.errors import MandatoryContextExceedsWindowError
from src.state.workspace import Workspace
from src.tools.registry import build_default_registry


def manager(tmp_path, window=375000, output=16384):
    profile = SimpleNamespace(context_window_tokens=window, max_output_tokens=output, snapshot=lambda: {"id": "test"})
    return ContextManager(Workspace.build(tmp_path), object(), build_default_registry(), model_profile=profile, actual_max_new_tokens=output, tokenizer=TokenizerAdapter())


def test_pressure_boundaries():
    assert [calculate_pressure(x, 100)["level"] for x in (59, 60, 74, 75, 84, 85, 94, 95)] == [0, 1, 1, 2, 2, 3, 3, 4]
    assert calculate_pressure(0, 0)["level"] == 0
    assert calculate_pressure(1, 0)["level"] == 4


def test_task_goal_is_full_request_and_history(tmp_path):
    m = manager(tmp_path)
    memory = WorkingMemory(tmp_path)
    session = {"history": [{"kind": "user", "event_id": "e", "turn_id": "t", "content": "new task"}]}
    outcome = m.build(session, memory, "new task", allowed_tools=frozenset())
    assert outcome.working_memory_candidate.task_goal == "new task"
    assert any(item.get("content") == "new task" for item in outcome.context_result.provider_input.input)
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


def test_mandatory_capacity_failure(tmp_path):
    with pytest.raises(MandatoryContextExceedsWindowError):
        manager(tmp_path, window=100, output=80).build({"history": []}, WorkingMemory(tmp_path), "x" * 100, allowed_tools=frozenset())


def test_level_windows():
    assert [ContextManager._history_window_for_level(i) for i in range(5)] == [7, 5, 4, 3, 3]


@pytest.mark.parametrize(
    ("turn_ids", "expected"),
    [([], ""), (["turn-1"], "turn-1"), (["turn-1", "turn-2"], "turn-1"), (["turn-1", "turn-2", "turn-3"], "turn-1"), (["turn-1", "turn-2", "turn-3", "turn-4"], "turn-2")],
)
def test_compact_summary_turn_id_handles_short_history(turn_ids, expected):
    """历史回合不足三项时也必须生成合法的摘要锚点。"""
    assert ContextManager._compact_summary_turn_id(turn_ids) == expected
