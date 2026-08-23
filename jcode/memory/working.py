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
    subagent_results: list[str] = field(default_factory=list)
    durable_promotions: list[str] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, workspace_root: Path) -> "WorkingMemory":
        return cls(
            workspace_root=workspace_root,
            task_goal=str(data.get("task_goal", "")),
            constraints=list(data.get("constraints", [])),
            recent_files=list(data.get("recent_files", [])),
            file_freshness=dict(data.get("file_freshness", {})),
            tool_observations=list(data.get("tool_observations", [])),
            resume_context=dict(data.get("resume_context", {})),
            retrieved_memory=list(data.get("retrieved_memory", [])),
            subagent_results=list(data.get("subagent_results", [])),
            durable_promotions=list(data.get("durable_promotions", [])),
            safety_notes=list(data.get("safety_notes", [])),
        )

    def to_dict(self) -> dict:
        return {
            "task_goal": self.task_goal,
            "constraints": self.constraints,
            "recent_files": self.recent_files,
            "file_freshness": self.file_freshness,
            "tool_observations": self.tool_observations[-20:],
            "resume_context": self.resume_context,
            "retrieved_memory": self.retrieved_memory,
            "subagent_results": self.subagent_results[-10:],
            "durable_promotions": self.durable_promotions[-20:],
            "safety_notes": self.safety_notes[-20:],
        }

    def note_file_read(self, relpath: str, freshness: str) -> None:
        if relpath not in self.recent_files:
            self.recent_files.append(relpath)
        self.file_freshness[relpath] = freshness

    def observe_tool(self, text: str) -> None:
        self.tool_observations.append(text[:1000])

    def note_safety(self, text: str) -> None:
        self.safety_notes.append(text[:1000])

    def render(self) -> str:
        lines = ["Working memory:"]
        lines.append(f"- task_goal: {self.task_goal or '(not set)'}")
        if self.constraints:
            lines.append("- constraints: " + "; ".join(self.constraints))
        if self.recent_files:
            lines.append("- recent_files: " + ", ".join(self.recent_files[-10:]))
        if self.file_freshness:
            freshness = ", ".join(f"{k}={v}" for k, v in list(self.file_freshness.items())[-10:])
            lines.append("- file_freshness: " + freshness)
        if self.resume_context:
            lines.append("- resume_context: " + str(self.resume_context)[:1000])
        if self.retrieved_memory:
            lines.append("- retrieved_memory:\n" + "\n".join(f"  - {x}" for x in self.retrieved_memory[:5]))
        if self.subagent_results:
            lines.append("- subagent_results:\n" + "\n".join(f"  - {x}" for x in self.subagent_results[-5:]))
        if self.tool_observations:
            lines.append("- recent_tool_observations:\n" + "\n".join(f"  - {x}" for x in self.tool_observations[-5:]))
        if self.safety_notes:
            lines.append("- safety_notes:\n" + "\n".join(f"  - {x}" for x in self.safety_notes[-5:]))
        return "\n".join(lines)
