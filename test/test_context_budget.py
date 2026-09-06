from types import SimpleNamespace

from src.context.budget import ContextBudgetCandidate, ReasoningContinuationBudgetOccupant, TokenizerAdapter, ToolContinuationBudgetOccupant, pack_budget_candidates, serialize_counted_input
from src.context.manager import ContextManager
from src.memory.working import WorkingMemory
from src.state.workspace import Workspace
from src.tools.registry import build_default_registry


def test_empty_continuation_occupants_are_zero():
    tokenizer = TokenizerAdapter()
    assert ReasoningContinuationBudgetOccupant([], tokenizer).tokens == 0
    assert ToolContinuationBudgetOccupant([], tokenizer).tokens == 0


def test_serialized_input_occupancies_partition_actual_input():
    tokenizer = TokenizerAdapter()
    serialized, occupancies = serialize_counted_input("rules", [{"role": "user", "content": "task"}], [{"type": "function", "name": "f"}], tokenizer)
    assert sum(item.tokens for item in occupancies) == tokenizer.count(serialized)
    assert next(item for item in occupancies if item.name == "instructions").tokens > 0
    assert next(item for item in occupancies if item.name == "tools_schema").tokens > 0


def test_candidate_packing_is_stable_and_rejects_oversized_optional_items():
    candidates = [
        ContextBudgetCandidate("history", "late", "late", 2, token_increment=6),
        ContextBudgetCandidate("skills", "core", "core", 0, priority_class=3, token_increment=4),
        ContextBudgetCandidate("history", "early", "early", 1, token_increment=5),
    ]
    selected, decisions = pack_budget_candidates(candidates, 10, required_names=["core"])
    assert [item.stable_name for item in selected] == ["core", "early"]
    assert any(item["candidate"] == "late" and item["action"] == "reject" for item in decisions)


def test_list_files_window_merges_last_pair_and_removes_orphan(tmp_path):
    profile = SimpleNamespace(context_window_tokens=150000, max_output_tokens=16384, snapshot=lambda: {})
    manager = ContextManager(Workspace.build(tmp_path), object(), build_default_registry(), model_profile=profile)
    history = []
    for index in range(1, 8):
        turn = f"t{index}"
        history.append({"kind": "user", "event_id": f"u{index}", "turn_id": turn, "content": turn})
    history.extend([
        {"kind": "tool_call", "event_id": "c-old", "turn_id": "t1", "tool_name": "list_files", "call_id": "c-old", "arguments": {"path": "src"}},
        {"kind": "tool_result", "event_id": "r-old", "turn_id": "t1", "tool_name": "list_files", "call_id": "c-old", "content": "old"},
        {"kind": "tool_call", "event_id": "c-new", "turn_id": "t2", "tool_name": "list_files", "call_id": "c-new", "arguments": {"path": "src"}},
        {"kind": "tool_result", "event_id": "r-new", "turn_id": "t2", "tool_name": "list_files", "call_id": "c-new", "content": "new"},
        {"kind": "tool_call", "event_id": "orphan", "turn_id": "t1", "tool_name": "read_file", "call_id": "orphan", "arguments": {"path": "x"}},
    ])
    selected, _ = manager._build_structured_history({"history": history}, pressure_level=1)
    ids = [event.event_id for event in selected]
    assert "c-new" in ids and "r-new" in ids
    assert "c-old" not in ids and "r-old" not in ids
    assert "orphan" not in ids


def test_internal_continuation_instruction_is_sent_without_replacing_goal(tmp_path):
    profile = SimpleNamespace(context_window_tokens=150000, max_output_tokens=16384, snapshot=lambda: {})
    manager = ContextManager(Workspace.build(tmp_path), object(), build_default_registry(), model_profile=profile)
    memory = WorkingMemory(tmp_path)
    memory.task_goal = "original task"
    outcome = manager.build({"history": []}, memory, "[Continuation Required]\nContinue", allowed_tools=frozenset())
    contents = [item.get("content", "") for item in outcome.context_result.provider_input.input]
    assert "original task" in outcome.working_memory_candidate.task_goal
    assert any("[Continuation Required]" in str(content) for content in contents)
