from __future__ import annotations

from pathlib import Path

from src.app.web_events import trace_events
from src.app.web_steps import build_reasoning_steps
from src.state.session import SessionStore


def build_session_turns(project_id: str, project_root: Path, session: dict) -> dict:
    runs_root = project_root / ".jcode" / "runs"
    history = list(session.get("history", []) or [])
    history_turn_ids = _resolved_history_turn_ids(history)
    run_ids = _ordered_run_ids(session, history_turn_ids)
    # history 是会话内唯一的原始顺序；不能用 run_ids 覆盖它，否则重载会打乱消息位置。
    history_positions = _history_run_positions(history_turn_ids)
    turns = [_build_turn(run_id, runs_root, history, history_turn_ids) for run_id in run_ids]
    turns.extend(_orphan_history_turns(history, history_turn_ids))
    turns.sort(key=lambda turn: _turn_sort_key(turn, history_positions))
    for sequence, turn in enumerate(turns):
        turn["sequence"] = sequence
    return {"project_id": project_id, "session_id": session.get("id", ""), "turns": turns}


def _ordered_run_ids(session: dict, history_turn_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    run_ids: list[str] = []
    for value in history_turn_ids:
        if value and value not in seen:
            run_ids.append(value)
            seen.add(value)
    # 没有历史消息的运行只能追加，不能插入已知会话时间线中间。
    for run_id in session.get("run_ids", []) or []:
        value = str(run_id or "").strip()
        if value and value not in seen:
            run_ids.append(value)
            seen.add(value)
    return run_ids


def _history_run_positions(history_turn_ids: list[str]) -> dict[str, int]:
    """记录每个运行在会话消息流中首次出现的位置。"""
    positions: dict[str, int] = {}
    for index, run_id in enumerate(history_turn_ids):
        if run_id and run_id not in positions:
            positions[run_id] = index
    return positions


def _resolved_history_turn_ids(history: list[dict]) -> list[str]:
    """解析历史项的运行归属，兼容中途确认消息只携带 turn_id 的存储格式。"""
    next_named_turn_ids = [""] * len(history)
    next_turn_id = ""
    for index in range(len(history) - 1, -1, -1):
        named_turn_id = _named_turn_id(history[index])
        if named_turn_id:
            next_turn_id = named_turn_id
        next_named_turn_ids[index] = next_turn_id

    resolved_turn_ids: list[str] = []
    previous_turn_id = ""
    for index, item in enumerate(history):
        turn_id = _named_turn_id(item)
        if not turn_id and str(item.get("turn_id") or "").strip() == "current":
            # “current” 是中途确认回复，归属到后续正在继续执行的同一运行。
            turn_id = next_named_turn_ids[index] or previous_turn_id
        resolved_turn_ids.append(turn_id)
        if turn_id:
            previous_turn_id = turn_id
    return resolved_turn_ids


def _named_turn_id(item: dict) -> str:
    """从当前协议的 run_id 或明确 turn_id 中取得运行标识。"""
    run_id = str(item.get("run_id") or "").strip()
    if run_id:
        return run_id
    turn_id = str(item.get("turn_id") or "").strip()
    return turn_id if turn_id and turn_id != "current" else ""


def _turn_sort_key(turn: dict, history_positions: dict[str, int]) -> tuple[int, int, str]:
    """用历史索引保持对话先后，缺少历史的运行稳定放在末尾。"""
    run_id = str(turn.get("run_id") or "")
    position = history_positions.get(run_id)
    if position is None and run_id.startswith("history-"):
        position = int(run_id.removeprefix("history-") or 0)
    if position is None:
        return (1, 0, run_id)
    return (0, position, run_id)


def _build_turn(run_id: str, runs_root: Path, history: list[dict], history_turn_ids: list[str]) -> dict:
    items = [item for item, history_turn_id in zip(history, history_turn_ids) if history_turn_id == run_id]
    events = trace_events(runs_root / run_id)
    reasoning_steps, final_text = build_reasoning_steps(events, run_id=run_id)
    if not reasoning_steps:
        reasoning_steps = _fallback_steps(items, run_id)
    user_message = _first_content(items, "user")
    assistant_message = _last_content(items, "assistant")
    changed_files = _changed_files(events)
    if not final_text:
        final_text = assistant_message
    return {
        "run_id": run_id,
        "user_message": user_message,
        "assistant_message": assistant_message,
        "final_text": final_text,
        "reasoning_steps": reasoning_steps,
        "status": _status(events, assistant_message),
        "event_count": len(events),
        "step_count": len(reasoning_steps),
        "tool_count": sum(len(step.get("tool_calls", []) or []) for step in reasoning_steps),
        "changed_files": changed_files,
        "events": events,
    }


def _orphan_history_turns(history: list[dict], history_turn_ids: list[str]) -> list[dict]:
    turns: list[dict] = []
    for index, (item, history_turn_id) in enumerate(zip(history, history_turn_ids)):
        if history_turn_id or item.get("kind") == "tool_result":
            continue
        role = str(item.get("kind") or "message")
        content = str(item.get("content") or "")
        turns.append(
            {
                "run_id": f"history-{index}",
                "user_message": content if role == "user" else "",
                "assistant_message": content if role == "assistant" else "",
                "final_text": content if role == "assistant" else "",
                "reasoning_steps": [],
                "status": "history",
                "event_count": 0,
                "step_count": 0,
                "tool_count": 0,
                "changed_files": [],
                "events": [],
            }
        )
    return turns


def _fallback_steps(items: list[dict], run_id: str) -> list[dict]:
    if not items:
        return []
    return [
        {
            "step_id": f"{run_id}:1" if run_id else "step-1",
            "index": 1,
            "timestamp": items[0].get("created_at", "") if items else "",
            "end_timestamp": items[-1].get("created_at", "") if items else "",
            "status": "success",
            "context_audit_ref": "",
            "response_text": "",
            "process_content": "",
            "error_text": "",
            "parsed_action": {},
            "tool_calls": [],
            "details": [],
            "duration_ms": None,
            "tool_count": 0,
        }
    ]


def _first_content(items: list[dict], role: str) -> str:
    for item in items:
        if item.get("kind") == role:
            return str(item.get("content") or "")
    return ""


def _last_content(items: list[dict], role: str) -> str:
    for item in reversed(items):
        if item.get("kind") == role:
            return str(item.get("content") or "")
    return ""


def _changed_files(events: list[dict]) -> list[str]:
    changed: list[str] = []
    known: set[str] = set()
    for event in events:
        for path in event.get("changed_files", []) or []:
            value = str(path)
            if value not in known:
                changed.append(value)
                known.add(value)
    return changed


def _status(events: list[dict], assistant_message: str) -> str:
    for event in reversed(events):
        if event.get("event") == "run_failed":
            return "failed"
        if event.get("event") == "run_aborted":
            return "stopped"
        if event.get("event") == "run_finished":
            status = str(event.get("status") or "")
            stop_reason = str(event.get("stop_reason") or "")
            if status == "completed":
                return "completed"
            if stop_reason:
                return "stopped"
    if assistant_message:
        return "completed"
    if events:
        return "incomplete"
    return "history"


def session_with_turns(project_id: str, project_root: Path, session_store: SessionStore, session_id: str) -> dict:
    session = session_store.load_requested(session_id, None, project_root)
    return build_session_turns(project_id, project_root, session)
