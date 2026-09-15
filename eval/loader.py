from __future__ import annotations

from pathlib import Path
import json

from eval.protocol import EvalCase


class CaseLoader:
    """集中加载和索引评测 Case。"""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def list_cases(self) -> list[Path]:
        """按稳定路径顺序返回所有 JSON Case。"""
        return sorted(self.root.rglob("*.json"))

    def load_all(self) -> list[EvalCase]:
        return [EvalCase.load(path) for path in self.list_cases()]

    def validate(self) -> list[str]:
        """校验全部 Case，返回错误而不是静默跳过非法数据。"""
        errors: list[str] = []
        for path in self.list_cases():
            try:
                EvalCase.load(path)
            except (OSError, ValueError, TypeError) as exc:
                errors.append(f"{path}: {exc}")
        return errors

    def find(self, case_id: str) -> EvalCase:
        for case in self.load_all():
            if case.case_id == case_id:
                return case
        raise KeyError(f"unknown eval case: {case_id}")

    def load_manifest(self, path: str | Path) -> list[EvalCase]:
        """按 manifest 声明顺序加载版本化 Case。"""
        manifest_path = Path(path)
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("cases"), list):
            raise ValueError("manifest requires a cases array")
        cases: list[EvalCase] = []
        for item in value["cases"]:
            case_path = (manifest_path.parent.parent / "cases" / str(item)).resolve()
            cases.append(EvalCase.load(case_path))
        return cases
