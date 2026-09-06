from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkingMemory:
    workspace_root: Path
    task_goal: str = ""
    constraints: list[str] = field(default_factory=list)
    recent_files: list[str] = field(default_factory=list)
    file_freshness: dict[str, str] = field(default_factory=dict)
    read_file_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    file_reads: dict[str, dict[str, dict]] = field(default_factory=dict)  # 按文件版本累计的读取范围与状态
    tool_observations: list[dict] = field(default_factory=list)
    resume_context: dict = field(default_factory=dict)
    retrieved_memory: list[str] = field(default_factory=list)
    last_retrieval_query: str = ""
    subagent_results: list[str] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=list)
    compact_summary: str = ""
    runtime_context: str = ""  # 当前运行模式与工作区上下文
    todo_items: list[dict] = field(default_factory=list)  # 当前会话 todo 的只读投影
    cold_files: dict[str, dict] = field(default_factory=dict)  # Level 4 归档的冷文件索引
    runtime_state: dict = field(default_factory=dict)  # 运行时状态快照

    @classmethod
    def from_dict(cls, data: dict, workspace_root: Path) -> "WorkingMemory":
        if not isinstance(data, dict):
            data = {}
        if data and data.get("schema") not in {None, "jcode.layered_memory.v2"}:
            raise ValueError("working memory schema mismatch")
        task = data.get("core", {}) if isinstance(data.get("core"), dict) else {}
        files = data.get("files", {}) if isinstance(data.get("files"), dict) else {}
        retrieval = data.get("retrieval", {}) if isinstance(data.get("retrieval"), dict) else {}
        tools = data.get("tools", {}) if isinstance(data.get("tools"), dict) else {}
        safety = data.get("safety", {}) if isinstance(data.get("safety"), dict) else {}
        compact = data.get("compact", {}) if isinstance(data.get("compact"), dict) else {}
        read_file_counts = _read_file_counts_from_dict(files.get("read_file_counts", {}))
        file_reads = _file_reads_from_dict(files.get("reads", {}))
        return cls(
            workspace_root=workspace_root,
            task_goal=str(task.get("task_goal", "")),
            constraints=list(task.get("constraints", [])),
            recent_files=list(files.get("hot", [])),
            file_freshness=dict(files.get("freshness", {})),
            read_file_counts=read_file_counts,
            file_reads=file_reads,
            tool_observations=[item for item in tools.get("observations", []) if isinstance(item, dict)],
            resume_context=dict(task.get("resume_context", {})),
            retrieved_memory=list(retrieval.get("items", [])),
            last_retrieval_query=str(retrieval.get("last_query", "")),
            subagent_results=list(tools.get("subagent_results", [])),
            safety_notes=list(safety.get("notes", [])),
            compact_summary=str(compact.get("summary", "")),
            runtime_context=str(data.get("runtime_context", "")),
            todo_items=[item for item in (data.get("todo", {}).get("items", []) if isinstance(data.get("todo", {}), dict) else []) if isinstance(item, dict)],
            cold_files={str(k): dict(v) for k, v in dict(files.get("cold", {})).items() if isinstance(v, dict)},
            runtime_state=dict(data.get("runtime_state", {})),
        )

    def to_dict(self) -> dict:
        return {
            "schema": "jcode.layered_memory.v2",
            "core": {
                "task_goal": self.task_goal,
                "constraints": self.constraints,
                "resume_context": self.resume_context,
            },
            "files": {
                "hot": self.recent_files[-20:],
                "cold": self.cold_files,
                "freshness": self.file_freshness,
                "read_file_counts": self.read_file_counts,
                "reads": self.file_reads,
            },
            "retrieval": {
                "last_query": self.last_retrieval_query,
                "items": self.retrieved_memory,
            },
            "tools": {
                "observations": self.tool_observations[-20:],
                "subagent_results": self.subagent_results[-10:],
            },
            "safety": {
                "notes": self.safety_notes[-20:],
            },
            "compact": {
                "summary": self.compact_summary,
            },
            "compact_summary": self.compact_summary,
            "runtime_context": self.runtime_context,
            "todo": {
                "items": [dict(item) for item in self.todo_items],
            },
            "runtime_state": dict(self.runtime_state),
        }

    def sync_todos(self, ledger: dict | None) -> None:
        """从 session ledger 刷新当前回合的 todo 投影。"""
        payload = ledger if isinstance(ledger, dict) else {}
        raw_items = payload.get("items", [])
        self.todo_items = [dict(item) for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []

    def note_file_read(self, relpath: str, args: dict, freshness: str, metadata: dict) -> None:
        if relpath not in self.recent_files:
            self.recent_files.append(relpath)
        self.file_freshness[relpath] = freshness
        # 这里只记录成功读到的文件版本，后续用它判断同一份老文件已经读了几次。
        bucket = self.read_file_counts.setdefault(relpath, {})
        key = _read_file_count_key(relpath, args, freshness)
        bucket[key] = int(bucket.get(key, 0)) + 1

        # 工作记忆只保存读取覆盖与完整性，不保存文件正文。
        versions = self.file_reads.setdefault(relpath, {})
        version = versions.setdefault(str(freshness), {"file_size": 0, "ranges": {}})
        version["file_size"] = int(metadata.get("file_size", 0) or 0)
        range_key = _read_range_key(args)
        ranges = version.setdefault("ranges", {})
        record = ranges.setdefault(
            range_key,
            {
                "start": int(args.get("start", 0)),
                "end": args.get("end", None),
                "max_chars": int(args.get("max_chars", 20000)),
                "returned_chars": 0,
                "missing_chars": 0,
                "complete": False,
                "read_count": 0,
                "complete_read_count": 0,
            },
        )
        record["returned_chars"] = int(metadata.get("returned_chars", 0) or 0)
        record["missing_chars"] = int(metadata.get("missing_chars", 0) or 0)
        record["complete"] = bool(metadata.get("complete", False))
        record["read_count"] = int(record.get("read_count", 0)) + 1
        if record["complete"]:
            record["complete_read_count"] = int(record.get("complete_read_count", 0)) + 1

    def read_file_count(self, relpath: str, args: dict, freshness: str) -> int:
        bucket = self.read_file_counts.get(relpath, {})
        return int(bucket.get(_read_file_count_key(relpath, args, freshness), 0))

    def read_file_complete_count(self, relpath: str, args: dict, freshness: str) -> int:
        """返回同一文件版本与范围的完整读取次数。"""
        record = self.file_reads.get(relpath, {}).get(str(freshness), {}).get("ranges", {}).get(_read_range_key(args), {})
        return int(record.get("complete_read_count", 0)) if isinstance(record, dict) else 0

    def observe_tool(self, tool_name: str, status: str, summary: str, artifact_ref: str = "") -> None:
        """仅保存工具状态、关键摘要和 artifact 引用，避免复制正文。"""
        if tool_name in {"enter_plan_mode", "exit_plan_mode"}:
            return
        self.tool_observations.append({"tool": tool_name, "status": status, "summary": _head_tail_summary(str(summary)), "artifact": artifact_ref})

    def set_retrieval(self, query: str, items: list[str]) -> None:
        self.last_retrieval_query = query
        self.retrieved_memory = list(items)

    def note_safety(self, text: str) -> None:
        self.safety_notes.append(text[:1000])

    def set_compact_summary(self, text: str) -> None:
        self.compact_summary = str(text or "")

    def render(self) -> str:
        if self.resume_context:
            lines = ["Checkpoint:"]
            lines.append(str(self.resume_context))

        lines = ["Working_Memory:"]
        if self.task_goal:
            lines.append(f"- goal: {self.task_goal}")
        if self.constraints:
            lines.append("- constraints: " + "; ".join(self.constraints))
        if self.recent_files:
            lines.append("- recent_files: " + ", ".join(self.recent_files[-10:]))
        if self.file_freshness:
            freshness = ", ".join(f"{k}={v}" for k, v in list(self.file_freshness.items())[-10:])
            lines.append("- file_freshness: " + freshness)
        file_read_lines = self._render_current_file_reads()
        if file_read_lines:
            lines.append("- file_reads:\n" + "\n".join(file_read_lines))
        if self.last_retrieval_query:
            lines.append("- last_query: " + self.last_retrieval_query[:200])
        if self.retrieved_memory:
            lines.append("- retrieved_memory:\n" + "\n".join(f"  - {x}" for x in self.retrieved_memory[:5]))
        if self.subagent_results:
            lines.append("- subagent_results:\n" + "\n".join(f"  - {x}" for x in self.subagent_results[-5:]))
        if self.tool_observations:
            lines.append("- recent_tool_observations:\n" + "\n".join(f"  - {item['tool']} ({item['status']}): {item['summary']} {item['artifact']}" for item in self.tool_observations[-5:]))
        #lines.append("[compact]")
        #if self.compact_summary:这里的压缩摘要是哪里的？历史对话的？先不写这个。 
        #    lines.append("- summary:\n" + "\n".join(f"  - {x}" for x in self.compact_summary.splitlines()[:8]))
        if self.safety_notes:
            lines.append("- safety_notes:\n" + "\n".join(f"  - {x}" for x in self.safety_notes[-5:]))
        if self.runtime_context:
            lines.append("- runtime_context:\n" + self.runtime_context)
        if self.todo_items:
            completed = sum(1 for item in self.todo_items if str(item.get("status", "pending")) == "completed")
            in_progress = sum(1 for item in self.todo_items if str(item.get("status", "pending")) == "in_progress")
            pending = len(self.todo_items) - completed - in_progress
            percent = int(completed * 100 / len(self.todo_items))
            lines.append(f"- todo_progress: {completed}/{len(self.todo_items)} completed ({percent}%), {in_progress} in progress, {pending} pending")
            lines.append("- todos:\n" + "\n".join(
                f"  - {item.get('todo_id', '')} [{item.get('status', 'pending')}] {item.get('priority', 'normal')} - {item.get('content', '')}"
                + (f" | note: {item['note']}" if item.get("note") else "")
                for item in self.todo_items
            ))
        return "\n".join(lines)

    def _render_current_file_reads(self) -> list[str]:
        """只展示当前 freshness 的读取范围，旧版本仍保存在持久化数据中。"""
        lines: list[str] = []
        for relpath in self.recent_files[-10:]:
            freshness = self.file_freshness.get(relpath, "")
            version = self.file_reads.get(relpath, {}).get(freshness, {})
            ranges = version.get("ranges", {}) if isinstance(version, dict) else {}
            if not isinstance(ranges, dict) or not ranges:
                continue
            lines.append(f"  - {relpath} [freshness={freshness}; file_size={int(version.get('file_size', 0))} bytes]")
            for record in ranges.values():
                if not isinstance(record, dict):
                    continue
                end = record.get("end")
                end_text = "EOF" if end is None else str(end)
                lines.append(
                    "    - "
                    f"range: {record.get('start', 0)}..{end_text}; max_chars: {record.get('max_chars', 0)}; "
                    f"returned_chars: {record.get('returned_chars', 0)}; missing_chars: {record.get('missing_chars', 0)}; "
                    f"complete: {str(bool(record.get('complete', False))).lower()}; reads: {record.get('read_count', 0)}"
                )
        return lines


def _read_file_counts_from_dict(raw: object) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    if not isinstance(raw, dict):
        return counts
    for relpath, versions in raw.items():
        if not isinstance(versions, dict):
            continue
        rel = str(relpath)
        bucket: dict[str, int] = {}
        for freshness, count in versions.items():
            try:
                bucket[str(freshness)] = max(0, int(count))
            except Exception:
                continue
        if bucket:
            counts[rel] = bucket
    return counts


def _read_file_count_key(relpath: str, args: dict, freshness: str) -> str:
    payload = {
        "tool": "read_file",
        "path": relpath,
        "args": {
            "max_chars": int(args.get("max_chars", 20000)),
            "start": int(args.get("start", 0)),
            "end": args.get("end", None),
        },
        "freshness": str(freshness),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _read_range_key(args: dict) -> str:
    return json.dumps(
        {
            "max_chars": int(args.get("max_chars", 20000)),
            "start": int(args.get("start", 0)),
            "end": args.get("end", None),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _file_reads_from_dict(raw: object) -> dict[str, dict[str, dict]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, dict]] = {}
    for relpath, versions in raw.items():
        if not isinstance(versions, dict):
            continue
        clean_versions: dict[str, dict] = {}
        for freshness, version in versions.items():
            if not isinstance(version, dict) or not isinstance(version.get("ranges"), dict):
                continue
            clean_versions[str(freshness)] = {
                "file_size": int(version.get("file_size", 0) or 0),
                "ranges": {str(key): dict(record) for key, record in version["ranges"].items() if isinstance(record, dict)},
            }
        if clean_versions:
            result[str(relpath)] = clean_versions
    return result


def _head_tail_summary(text: str) -> str:
    if len(text) <= 1000:
        return text
    return text[:500] + "\n[...middle omitted...]\n" + text[-500:]
