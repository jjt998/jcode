from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path


class WorkspaceSandbox:
    """为每个 Case 创建隔离 workspace，并计算前后文件差异。"""

    def __init__(self, fixture: str | Path | None = None, root: str | Path | None = None):
        self.fixture = Path(fixture).resolve() if fixture else None
        self.root = Path(root).resolve() if root else Path(tempfile.mkdtemp(prefix="jcode-eval-"))
        self._initial: dict[str, str] = {}

    def prepare(self) -> Path:
        """复制 fixture 并记录初始文件哈希。"""
        self.root.mkdir(parents=True, exist_ok=True)
        if self.fixture:
            if not self.fixture.is_dir():
                raise FileNotFoundError(f"workspace fixture not found: {self.fixture}")
            for item in self.fixture.iterdir():
                target = self.root / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
        self._initial = self.snapshot()
        return self.root

    def snapshot(self) -> dict[str, str]:
        """计算 workspace 内文件内容哈希，排除运行证据目录。"""
        result: dict[str, str] = {}
        if not self.root.exists():
            return result
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or ".jcode" in path.parts:
                continue
            relative = str(path.relative_to(self.root)).replace("\\", "/")
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def fingerprint(self) -> str:
        payload = json.dumps(self.snapshot(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def changed_files(self) -> list[str]:
        current = self.snapshot()
        return sorted(path for path in set(self._initial) | set(current) if self._initial.get(path) != current.get(path))

    def cleanup(self) -> None:
        """清理当前沙箱；保留现场由调用方负责复制。"""
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=False)

