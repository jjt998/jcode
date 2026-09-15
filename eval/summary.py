from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def summarize_scores(score_paths: list[str | Path]) -> dict[str, Any]:
    """汇总多个离线评分文件，保留类别和红线明细。"""
    scores = [json.loads(Path(path).read_text(encoding="utf-8")) for path in score_paths]
    by_category: dict[str, list[dict[str, Any]]] = {}
    for score in scores:
        category = str(score.get("category") or str(score.get("case_id", "")).split("-")[0])
        by_category.setdefault(category, []).append(score)
    return {
        "total": len(scores),
        "passed": sum(1 for score in scores if score.get("passed") is True),
        "failed": sum(1 for score in scores if score.get("passed") is not True),
        "redlines": sum(len(score.get("redlines", [])) for score in scores),
        "quality_levels": {
            "agent": Counter(score.get("agent_quality", {}).get("level", "unknown") for score in scores),
            "harness": Counter(score.get("harness_quality", {}).get("level", "unknown") for score in scores),
        },
        "categories": {
            category: {
                "total": len(items),
                "passed": sum(1 for item in items if item.get("passed") is True),
            }
            for category, items in sorted(by_category.items())
        },
    }
