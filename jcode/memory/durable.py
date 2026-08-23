from __future__ import annotations

import json
from pathlib import Path

from jcode.state.workspace import now_iso


class DurableMemoryStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = root / "notes.jsonl"

    def add(self, text: str, source: str = "manual") -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"text": text, "source": source, "updated_at": now_iso()}, ensure_ascii=False) + "\n")

    def retrieve(self, query: str, limit: int = 5, max_chars: int = 2000) -> list[str]:
        if not self.path.exists():
            return []
        terms = {term.lower() for term in query.replace("/", " ").replace("\\", " ").split() if len(term) >= 2}
        scored: list[tuple[int, str]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = str(item.get("text", ""))
            score = sum(1 for term in terms if term in text.lower())
            if score or not terms:
                scored.append((score, text))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        result: list[str] = []
        used = 0
        for _, text in scored[:limit]:
            if used + len(text) > max_chars:
                break
            result.append(text)
            used += len(text)
        return result
