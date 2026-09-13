from __future__ import annotations

from datetime import date, datetime
import json
import uuid
from pathlib import Path

ENTRYPOINT_NAME = "MEMORY.md"
DAILY_LOG_ENTRIES_NAME = "entries.jsonl"


def ensure_memory_dir(root: Path) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "topics").mkdir(parents=True, exist_ok=True)
    index = root / ENTRYPOINT_NAME
    if not index.exists():
        index.write_text("# Durable Memory Index\n\n_Empty._\n", encoding="utf-8")
    return root


def daily_log_path(root: Path, today: date | None = None) -> Path:
    today = today or date.today()
    root = ensure_memory_dir(root)
    path = root / "logs" / str(today.year) / f"{today.month:02d}" / f"{today.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_to_daily_log(root: Path, text: str, *, source: str = "turn", today: date | None = None) -> Path | None:
    text = str(text or "").strip()
    if not text:
        return None
    path = daily_log_path(root, today=today)
    timestamp = datetime.now().strftime("%H:%M")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"- [{timestamp}] ({source}) {text}\n")
    return path


def append_structured_daily_log(root: Path, entry: dict) -> str:
    """追加结构化过程条目，保留每轮记忆整理所需的来源证据。"""
    root = ensure_memory_dir(root)
    payload = dict(entry)
    payload.setdefault("entry_id", f"log-{uuid.uuid4().hex[:12]}")
    payload.setdefault("created_at", datetime.now().astimezone().isoformat())
    path = root / DAILY_LOG_ENTRIES_NAME
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return str(payload["entry_id"])


def iter_structured_daily_logs(root: Path) -> list[dict]:
    """按写入顺序读取结构化 Daily Log，损坏行直接报告为格式错误。"""
    path = Path(root) / DAILY_LOG_ENTRIES_NAME
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("daily log entry must be an object")
        entries.append(item)
    return entries


def iter_daily_log_entries(root: Path) -> list[str]:
    logs = Path(root) / "logs"
    if not logs.exists():
        return []
    entries: list[str] = []
    for path in sorted(logs.rglob("*.md")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("- "):
                entries.append(line[2:].strip())
    return entries
