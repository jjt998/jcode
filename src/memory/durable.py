from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

from src.memory.journal import ENTRYPOINT_NAME, append_to_daily_log, ensure_memory_dir, iter_daily_log_entries
from src.memory.safety import looks_sensitive
from src.state.workspace import now_iso

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

MEMORY_TYPES = {"user_preference", "project_convention", "key_decision"}


def _query_terms(query: str) -> list[str]:
    """提取适合本地词法检索的中英文、路径和命令片段。"""
    text = str(query or "").lower().replace("\\", "/")
    terms = set(re.findall(r"[a-z0-9_./-]{2,}|[\u4e00-\u9fff]", text))
    return sorted(terms, key=lambda value: (-len(value), value))


@dataclass
class MemoryRecord:
    memory_id: str  # 长期记忆唯一标识
    memory_type: str  # 用户偏好、项目约定或关键决策
    text: str  # 可被后续任务复用的结论
    status: str  # candidate、active、conflict、stale 或 superseded
    source_entry_id: str  # 产生该记忆的 Daily Log 条目
    source_session_id: str  # 来源会话
    source_run_id: str  # 来源运行
    evidence_refs: list[str]  # 关联的文件或审计证据
    created_at: str  # 创建时间
    updated_at: str  # 最后更新时间


@dataclass
class MemoryHit:
    memory_id: str  # 命中的长期记忆 ID
    memory_type: str  # 命中记忆类型
    text: str  # 命中的记忆正文
    score: int  # 词法匹配分数
    matched_terms: list[str]  # 命中的查询词
    source_entry_id: str  # 来源 Daily Log 条目


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
        record = MemoryRecord(f"mem-{uuid.uuid4().hex[:12]}", "project_convention", text.strip(), "active", "", "", "", [], now_iso(), now_iso())
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return True

    def append_candidate(self, *, memory_type: str, text: str, source_entry_id: str, session_id: str = "", run_id: str = "", evidence_refs: list[str] | None = None) -> dict:
        """按当前协议写入候选并根据重复、冲突规则决定最终状态。"""
        if memory_type not in MEMORY_TYPES or not text.strip() or looks_sensitive(text):
            return {"status": "rejected", "reason": "invalid_or_sensitive"}
        records = self._records()
        same = [item for item in records if item.text == text.strip() and item.status == "active"]
        if same:
            return {"status": "duplicate", "memory_id": same[0].memory_id}
        candidate_terms = set(_query_terms(text))
        conflict = [
            item for item in records
            if item.memory_type == memory_type
            and item.status == "active"
            and item.text != text.strip()
            and candidate_terms.intersection(_query_terms(item.text))
        ]
        status = "conflict" if conflict else "active"
        record = MemoryRecord(f"mem-{uuid.uuid4().hex[:12]}", memory_type, text.strip(), status, source_entry_id, session_id, run_id, list(evidence_refs or []), now_iso(), now_iso())
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return {"status": status, "memory_id": record.memory_id, "conflict_ids": [item.memory_id for item in conflict]}

    def append_daily_log(self, text: str, source: str = "turn") -> str:
        if not text.strip() or looks_sensitive(text):
            return ""
        path = append_to_daily_log(self.root, text, source=source)
        return str(path) if path else ""

    def retrieve(self, query: str, limit: int = 5, max_chars: int = 2000) -> list[dict]:
        terms = _query_terms(query)
        scored: list[MemoryHit] = []
        for record in self._records():
            if record.status != "active":
                continue
            matched = [term for term in terms if term in record.text.lower()]
            if matched or not terms:
                scored.append(MemoryHit(record.memory_id, record.memory_type, record.text, len(matched), matched, record.source_entry_id))
        scored.sort(key=lambda item: (-item.score, item.memory_id))
        result: list[dict] = []
        used = 0
        for hit in scored[:limit]:
            if used + len(hit.text) > max_chars:
                break
            result.append(asdict(hit))
            used += len(hit.text)
        return result

    def _records(self) -> list[MemoryRecord]:
        records = []
        if not self.path.exists():
            return records
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("memory record must be an object")
            records.append(MemoryRecord(**item))
        return records

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
