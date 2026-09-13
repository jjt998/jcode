from pathlib import Path

from src.memory.consolidation import extract_memory_candidates, maintain_after_turn
from src.memory.durable import DurableMemoryStore
from src.memory.journal import iter_structured_daily_logs
from src.memory.retrieval import retrieve_into_working_memory
from src.memory.working import WorkingMemory


def test_explicit_memory_is_promoted_and_retrieved(tmp_path: Path):
    store = DurableMemoryStore(tmp_path / ".jcode" / "memory")
    memory = WorkingMemory(tmp_path)
    agent = type("Agent", (), {"session": {"id": "session-1"}})()
    task = type("Task", (), {"run_id": "run-1", "changed_files": [], "verification": {}, "unresolved_tool_failures": []})()

    audit = maintain_after_turn(store, memory, "本项目规定 测试统一使用 pytest", "已记录。", agent=agent, task_state=task)

    assert audit["durable_memory"]["promoted_count"] == 1
    assert len(iter_structured_daily_logs(store.root)) == 1
    hits = retrieve_into_working_memory(store, memory, "请按 pytest 运行测试")
    assert hits and hits[0]["memory_type"] == "project_convention"


def test_non_explicit_text_does_not_create_candidate():
    assert extract_memory_candidates("这次请使用 pytest") == []
    assert extract_memory_candidates("建议以后都使用 pytest") == []


def test_duplicate_and_conflict_are_explicit(tmp_path: Path):
    store = DurableMemoryStore(tmp_path / "memory")
    first = store.append_candidate(memory_type="key_decision", text="采用文件型存储", source_entry_id="log-1")
    duplicate = store.append_candidate(memory_type="key_decision", text="采用文件型存储", source_entry_id="log-2")
    conflict = store.append_candidate(memory_type="key_decision", text="采用数据库存储", source_entry_id="log-3")

    assert first["status"] == "active"
    assert duplicate["status"] == "duplicate"
    assert conflict["status"] == "conflict"
    assert [item["memory_id"] for item in store.retrieve("存储", limit=10)] == [first["memory_id"]]
