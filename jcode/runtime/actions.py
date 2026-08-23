from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class ModelAction:
    kind: str
    content: str = ""
    tool_name: str = ""
    tool_args: dict | None = None


FINAL_RE = re.compile(r"<final>(.*?)</final>", re.DOTALL)
TOOL_RE = re.compile(r"<tool\s+name=[\"']([^\"']+)[\"']\s*>(.*?)</tool>", re.DOTALL)


def parse_model_action(text: str) -> ModelAction:
    final = FINAL_RE.search(text)
    if final:
        return ModelAction(kind="final", content=final.group(1).strip())
    tool = TOOL_RE.search(text)
    if tool:
        raw_args = tool.group(2).strip() or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            return ModelAction(kind="invalid", content=f"invalid tool json: {exc}")
        return ModelAction(kind="tool", tool_name=tool.group(1).strip(), tool_args=args)
    return ModelAction(kind="invalid", content="model output did not contain <tool> or <final>")
