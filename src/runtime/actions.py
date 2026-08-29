from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class ModelAction:
    kind: str
    content: str = ""
    tool_name: str = ""
    tool_args: dict | None = None
    tool_calls: list["ModelToolCall"] = field(default_factory=list)


@dataclass(frozen=True)
class ModelToolCall:
    name: str
    args: dict


FINAL_RE = re.compile(r"^\s*<final>(.*?)</final>\s*$", re.DOTALL)
TOOL_RE = re.compile(r"^\s*<tool\s+name=[\"']([^\"']+)[\"']\s*>(.*?)</tool>\s*$", re.DOTALL)
TOOLS_RE = re.compile(r"^\s*<tools>\s*(.*?)\s*</tools>\s*$", re.DOTALL)


def parse_model_action(text: str) -> ModelAction:
    final = FINAL_RE.fullmatch(text)
    if final:
        return ModelAction(kind="final", content=final.group(1).strip())

    tools = TOOLS_RE.fullmatch(text)
    if tools:
        raw_items = tools.group(1).strip() or "[]"
        try:
            items = json.loads(raw_items)
        except json.JSONDecodeError as exc:
            return ModelAction(kind="invalid", content=f"invalid tools json: {exc}")
        if not isinstance(items, list):
            return ModelAction(kind="invalid", content="invalid tools payload: expected a JSON array")
        tool_calls: list[ModelToolCall] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                return ModelAction(kind="invalid", content=f"invalid tools item at index {index}: expected an object")
            name = str(item.get("name", "")).strip()
            if not name:
                return ModelAction(kind="invalid", content=f"invalid tools item at index {index}: missing tool name")
            args = item.get("args", {})
            if args is None:
                args = {}
            if not isinstance(args, dict):
                return ModelAction(kind="invalid", content=f"invalid tools item at index {index}: args must be a JSON object")
            tool_calls.append(ModelToolCall(name=name, args=args))
        return ModelAction(kind="tools", tool_calls=tool_calls)

    tool = TOOL_RE.fullmatch(text)
    if tool:
        raw_args = tool.group(2).strip() or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            return ModelAction(kind="invalid", content=f"invalid tool json: {exc}")
        return ModelAction(kind="tool", tool_name=tool.group(1).strip(), tool_args=args)
    return ModelAction(kind="invalid", content="model output must contain exactly one of <tool>, <tools>, or <final>")
