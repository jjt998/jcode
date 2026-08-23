from __future__ import annotations

import json
import re
from pathlib import Path

from jcode.memory.journal import ENTRYPOINT_NAME, append_to_daily_log, ensure_memory_dir, iter_daily_log_entries
from jcode.memory.safety import looks_sensitive
from jcode.state.workspace import now_iso

TOPIC_DEFAULTS = {
    "project-conventions": {
        "title": "Project Conventions",
        "summary": "Stable repository conventions and coding style.",
        "tags": ["convention", "project"],
    },
    "key-decisions": {
        "title": "Key Decisions",
        "summary": "Long-lived decisions and rationale anchors.",
        "tags": ["decision"],
    },
    "dependency-facts": {
        "title": "Dependency Facts",
        "summary": "Stable dependency and environment facts.",
        "tags": ["dependency"],
    },
    "user-preferences": {
        "title": "User Preferences",
        "summary": "Stable user preferences and collaboration rules.",
        "tags": ["preference", "user"],
    },
}


class DurableMemoryStore:
    root: Path
    path: Path
    index_path: Path
    topics_dir: Path

    def __init__(self, root: Path):
        self.root = ensure_memory_dir(root)
        self.path = self.root / "notes.jsonl"
        self.index_path = self.root / ENTRYPOINT_NAME
        self.topics_dir = self.root / "topics"

    def add(self, text: str, source: str = "manual") -> bool:
        if not text.strip() or looks_sensitive(text):
            return False
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"text": text, "source": source, "updated_at": now_iso()}, ensure_ascii=False) + "\n")
        return True

    def append_daily_log(self, text: str, source: str = "turn") -> str:
        if not text.strip() or looks_sensitive(text):
            return ""
        path = append_to_daily_log(self.root, text, source=source)
        return str(path) if path else ""

    def retrieve(self, query: str, limit: int = 5, max_chars: int = 2000) -> list[str]:
        terms = {term.lower() for term in query.replace("/", " ").replace("\\", " ").split() if len(term) >= 2}
        scored: list[tuple[int, str]] = []
        for text in self._all_notes():
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

    def promote_from_turn(self, user_message: str, final_text: str) -> dict:
        candidates = []
        if final_text.strip():
            candidates.append(f"Task: {user_message[:300]}\nOutcome: {final_text[:1000]}")
        promoted = []
        log_paths = []
        for text in candidates:
            log_path = self.append_daily_log(text, source="turn_summary")
            if log_path:
                log_paths.append(log_path)
            if self.add(text, source="turn_summary"):
                promoted.append(text)
        return {
            "daily_log": {
                "enabled": True,
                "source": "turn_summary",
                "count": len(log_paths),
                "paths": log_paths,
            },
            "durable_memory": {
                "promoted_count": len(promoted),
                "promoted_preview": [text[:200] for text in promoted],
                "legacy_notes_jsonl": str(self.path),
            },
            "promoted": promoted,
        }

    def consolidate_daily_logs(self) -> dict:
        entries = iter_daily_log_entries(self.root)
        topic_notes: dict[str, list[str]] = {topic: self._load_topic_notes(topic) for topic in TOPIC_DEFAULTS}
        added = 0
        for entry in entries:
            note = self._clean_log_entry(entry)
            if not note or looks_sensitive(note):
                continue
            topic = self._classify(note)
            if note not in topic_notes[topic]:
                topic_notes[topic].append(note)
                added += 1
        self._write_index()
        for topic, notes in topic_notes.items():
            self._write_topic(topic, notes[-50:])
        return {"topics": sorted(topic_notes), "added": added}

    def _all_notes(self) -> list[str]:
        notes: list[str] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = str(item.get("text", "")).strip()
                if text:
                    notes.append(text)
        for topic in TOPIC_DEFAULTS:
            notes.extend(self._load_topic_notes(topic))
        return notes

    def _load_topic_notes(self, topic: str) -> list[str]:
        path = self.topics_dir / f"{topic}.md"
        if not path.exists():
            return []
        notes: list[str] = []
        capture = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped == "## Notes":
                capture = True
                continue
            if capture and stripped.startswith("- "):
                notes.append(stripped[2:].strip())
        return notes

    def _write_index(self) -> None:
        lines = ["# Durable Memory Index", ""]
        for topic, meta in TOPIC_DEFAULTS.items():
            lines.append(f"- [{topic}](topics/{topic}.md): {meta['title']}")
            lines.append(f"  - summary: {meta['summary']}")
            lines.append(f"  - tags: {', '.join(meta['tags'])}")
        self.index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _write_topic(self, topic: str, notes: list[str]) -> None:
        meta = TOPIC_DEFAULTS[topic]
        lines = [
            f"# {meta['title']}",
            "",
            f"- topic: {topic}",
            f"- summary: {meta['summary']}",
            f"- tags: {', '.join(meta['tags'])}",
            f"- updated_at: {now_iso()}",
            "",
            "## Notes",
        ]
        for note in notes:
            lines.append(f"- {note}")
        path = self.topics_dir / f"{topic}.md"
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    @staticmethod
    def _clean_log_entry(entry: str) -> str:
        text = re.sub(r"^\[[^\]]+\]\s*", "", entry).strip()
        text = re.sub(r"^\([^)]+\)\s*", "", text).strip()
        return text[:1200]

    @staticmethod
    def _classify(text: str) -> str:
        lowered = text.lower()
        if any(word in lowered for word in ("prefer", "preference", "用户", "偏好", "喜欢", "不要")):
            return "user-preferences"
        if any(word in lowered for word in ("decision", "decide", "chosen", "决策", "确定", "选择")):
            return "key-decisions"
        if any(word in lowered for word in ("dependency", "package", "version", "依赖", "环境")):
            return "dependency-facts"
        return "project-conventions"
