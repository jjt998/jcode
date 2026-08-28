from __future__ import annotations

import hashlib
import os
import subprocess
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DOC_NAMES = ("AGENTS.md", "README.md", "pyproject.toml", "package.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Workspace:
    root: Path
    cwd: Path
    repo_root: Path
    branch: str = "-"
    default_branch: str = "main"
    status: str = "clean"
    recent_commits: list[str] = field(default_factory=list)
    project_docs: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, cwd: str | Path) -> "Workspace":
        cwd_path = Path(cwd).resolve()

        def git(args: list[str], fallback: str = "") -> str:
            try:
                result = subprocess.run(
                    ["git", *args],
                    cwd=cwd_path,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                return result.stdout.strip() or fallback
            except Exception:
                return fallback

        repo_root = Path(git(["rev-parse", "--show-toplevel"], str(cwd_path))).resolve()
        if not repo_root.exists():
            repo_root = cwd_path
        docs = _collect_project_docs(repo_root, cwd_path)
        return cls(
            root=repo_root,
            cwd=cwd_path,
            repo_root=repo_root,
            branch=git(["branch", "--show-current"], "-") or "-",
            default_branch=_default_branch(git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], "origin/main")),
            status=git(["status", "--short"], "clean") or "clean",
            recent_commits=[line for line in git(["log", "--oneline", "-5"]).splitlines() if line],
            project_docs=docs,
        )

    def resolve_path(self, value: str | Path) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if os.path.commonpath([str(self.root), str(resolved)]) != str(self.root):
            raise ValueError(f"path escapes workspace: {value}")
        return resolved

    def relpath(self, path: str | Path) -> str:
        return str(Path(path).resolve().relative_to(self.root)).replace("\\", "/")

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(str(self.root).encode("utf-8"))
        for path in sorted(self.root.glob("*"))[:200]:
            if path.name == ".jcode":
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            h.update(f"{path.name}:{int(stat.st_mtime)}:{stat.st_size}".encode("utf-8"))
        return h.hexdigest()[:16]

    def snapshot(self) -> dict[str, tuple[int, int]]:
        items: dict[str, tuple[int, int]] = {}
        for path in self.root.rglob("*"):
            if ".jcode" in path.parts or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            items[self.relpath(path)] = (int(stat.st_mtime_ns), int(stat.st_size))
        return items

    def runtime_text(self) -> str:
        commits = "\n".join(f"- {line}" for line in self.recent_commits) or "- none"
        return textwrap.dedent(
            f"""\
            Workspace runtime:
            - cwd: {self.cwd}
            - repo_root: {self.repo_root}
            - branch: {self.branch}
            - default_branch: {self.default_branch}
            - status:
            {self.status}
            - recent_commits:
            {commits}
            """
        ).strip()

    def stable_docs_text(self) -> str:
        if not self.project_docs:
            return "Workspace docs:\n- none"
        lines = ["Workspace docs:"]
        for path, snippet in self.project_docs.items():
            lines.append(f"- {path}")
            lines.append(textwrap.indent(snippet, "  "))
        return "\n".join(lines).strip()

    def project_rules_text(self) -> str:
        path = self.root / "JCODE.md"
        if not path.is_file():
            return "Project rules from JCODE.md:\n(none)"
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) > 12_000:
            text = text[:12_000].rstrip() + "\n\n[truncated]"
        return "Project rules from JCODE.md:\n" + (text or "(empty)")

    def text(self) -> str:
        return "\n\n".join([self.runtime_text(), self.stable_docs_text()]).strip()

    def workspace_hash(self) -> str:
        payload = {
            "cwd": str(self.cwd),
            "repo_root": str(self.repo_root),
            "branch": self.branch,
            "default_branch": self.default_branch,
            "status": self.status,
            "recent_commits": list(self.recent_commits),
            "project_docs": dict(self.project_docs),
        }
        return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()


def _collect_project_docs(repo_root: Path, cwd: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    for base in (repo_root, cwd):
        for name in DOC_NAMES:
            path = base / name
            if not path.exists():
                continue
            try:
                rel = path.resolve().relative_to(repo_root)
                key = str(rel).replace("\\", "/")
            except Exception:
                key = path.name
            if key in docs:
                continue
            docs[key] = _clip(path.read_text(encoding="utf-8", errors="replace"), 1200)
    return docs


def _clip(text: str, limit: int) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _default_branch(value: str) -> str:
    branch = str(value or "origin/main")
    if branch.startswith("origin/"):
        branch = branch[len("origin/") :]
    return branch or "main"
