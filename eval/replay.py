from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def read_trace(result_dir: str | Path) -> Iterator[dict[str, Any]]:
    """按原始顺序只读回放 trace，不重新执行任何动作。"""
    root = Path(result_dir)
    matches = list(root.rglob("trace.jsonl"))
    if not matches:
        return
    for line in matches[0].read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def replay_summary(result_dir: str | Path) -> dict[str, Any]:
    """生成适合终端展示的事件统计和证据索引。"""
    events = list(read_trace(result_dir))
    return {
        "result_dir": str(Path(result_dir).resolve()),
        "event_count": len(events),
        "event_types": sorted({str(event.get("event", "")) for event in events if event.get("event")}),
        "events": events,
    }
