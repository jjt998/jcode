from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkingMemory:
    workspace_root: Path
    task_goal: str = ""
    constraints: list[str] = field(default_factory=list)
    recent_files: list[str] = field(default_factory=list)
    file_freshness: dict[str, str] = field(default_factory=dict)
    tool_observations: list[str] = field(default_factory=list)
    resume_context: dict = field(default_factory=dict)
    retrieved_memory: list[str] = field(default_factory=list)
    last_retrieval_query: str = ""
    subagent_results: list[str] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, workspace_root: Path) -> "WorkingMemory":
        task = data.get("task", {}) if isinstance(data.get("task"), dict) else {}
        files = data.get("files", {}) if isinstance(data.get("files"), dict) else {}
        retrieval = data.get("retrieval", {}) if isinstance(data.get("retrieval"), dict) else {}
        tools = data.get("tools", {}) if isinstance(data.get("tools"), dict) else {}
        safety = data.get("safety", {}) if isinstance(data.get("safety"), dict) else {}
        return cls(
            workspace_root=workspace_root,
            task_goal=str(task.get("goal", data.get("task_goal", ""))),
            constraints=list(task.get("constraints", data.get("constraints", []))),
            recent_files=list(files.get("recent", data.get("recent_files", []))),
            file_freshness=dict(files.get("freshness", data.get("file_freshness", {}))),
            tool_observations=list(tools.get("observations", data.get("tool_observations", []))),
            resume_context=dict(task.get("resume_context", data.get("resume_context", {}))),
            retrieved_memory=list(retrieval.get("items", data.get("retrieved_memory", []))),
            last_retrieval_query=str(retrieval.get("last_query", data.get("last_retrieval_query", ""))),
            subagent_results=list(tools.get("subagent_results", data.get("subagent_results", []))),
            safety_notes=list(safety.get("notes", data.get("safety_notes", []))),
        )

    def to_dict(self) -> dict:
        return {
            "schema": "jcode.layered_memory.v1",
            "task": {
                "goal": self.task_goal,
                "constraints": self.constraints,
                "resume_context": self.resume_context,
            },
            "files": {
                "recent": self.recent_files[-20:],
                "freshness": self.file_freshness,
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
        }

    def note_file_read(self, relpath: str, freshness: str) -> None:
        if relpath not in self.recent_files:
            self.recent_files.append(relpath)
        self.file_freshness[relpath] = freshness

    def observe_tool(self, text: str) -> None:
        self.tool_observations.append(text[:1000])

    def set_retrieval(self, query: str, items: list[str]) -> None:
        self.last_retrieval_query = query
        self.retrieved_memory = list(items)

    def note_safety(self, text: str) -> None:
        self.safety_notes.append(text[:1000])

    def render(self) -> str:
        lines = ["Working_Memory:"]
        lines.append("[task]")
        lines.append(f"- goal: {self.task_goal or '(not set)'}")
        if self.constraints:
            lines.append("- constraints: " + "; ".join(self.constraints))
        if self.resume_context:
            lines.append("- resume_context: " + str(self.resume_context)[:1000])
        lines.append("[files]")
        if self.recent_files:
            lines.append("- recent_files: " + ", ".join(self.recent_files[-10:]))
        if self.file_freshness:
            freshness = ", ".join(f"{k}={v}" for k, v in list(self.file_freshness.items())[-10:])
            lines.append("- file_freshness: " + freshness)
        lines.append("[retrieval]")
        if self.last_retrieval_query:
            lines.append("- last_query: " + self.last_retrieval_query[:200])
        if self.retrieved_memory:
            lines.append("- retrieved_memory:\n" + "\n".join(f"  - {x}" for x in self.retrieved_memory[:5]))
        lines.append("[tools]")
        if self.subagent_results:
            lines.append("- subagent_results:\n" + "\n".join(f"  - {x}" for x in self.subagent_results[-5:]))
        if self.tool_observations:
            lines.append("- recent_tool_observations:\n" + "\n".join(f"  - {x}" for x in self.tool_observations[-5:]))
        lines.append("[safety]")
        if self.safety_notes:
            lines.append("- safety_notes:\n" + "\n".join(f"  - {x}" for x in self.safety_notes[-5:]))
        return "\n".join(lines)
