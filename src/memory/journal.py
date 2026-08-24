from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

ENTRYPOINT_NAME = "MEMORY.md"


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
