from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class ModelAction:
    kind: str
    content: str = ""
    reasoning: str = ""
    raw_content: str = ""
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
REASONING_RE = re.compile(r"^\s*<reasoning>(.*?)</reasoning>\s*(.*)$", re.DOTALL)


def parse_model_action(text: str) -> ModelAction:
    reasoning = ""
    body = text
    reasoning_match = REASONING_RE.fullmatch(text)
    if reasoning_match:
        reasoning = reasoning_match.group(1).strip()
        body = reasoning_match.group(2)

    final = FINAL_RE.fullmatch(body)
    if final:
        return ModelAction(kind="final", content=final.group(1).strip(), reasoning=reasoning, raw_content=text)

    tools = TOOLS_RE.fullmatch(body)
    if tools:
        raw_items = tools.group(1).strip() or "[]"
        try:
            items = json.loads(raw_items)
        except json.JSONDecodeError as exc:
            return ModelAction(kind="invalid", content=f"invalid tools json: {exc}", reasoning=reasoning, raw_content=text)
        if not isinstance(items, list):
            return ModelAction(kind="invalid", content="invalid tools payload: expected a JSON array", reasoning=reasoning, raw_content=text)
        tool_calls: list[ModelToolCall] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                return ModelAction(kind="invalid", content=f"invalid tools item at index {index}: expected an object", reasoning=reasoning, raw_content=text)
            name = str(item.get("name", "")).strip()
            if not name:
                return ModelAction(kind="invalid", content=f"invalid tools item at index {index}: missing tool name", reasoning=reasoning, raw_content=text)
            args = item.get("args", {})
            if args is None:
                args = {}
            if not isinstance(args, dict):
                return ModelAction(kind="invalid", content=f"invalid tools item at index {index}: args must be a JSON object", reasoning=reasoning, raw_content=text)
            tool_calls.append(ModelToolCall(name=name, args=args))
        return ModelAction(kind="tools", content=body.strip(), reasoning=reasoning, raw_content=text, tool_calls=tool_calls)

    tool = TOOL_RE.fullmatch(body)
    if tool:
        raw_args = tool.group(2).strip() or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            return ModelAction(kind="invalid", content=f"invalid tool json: {exc}", reasoning=reasoning, raw_content=text)
        return ModelAction(kind="tool", content=body.strip(), reasoning=reasoning, raw_content=text, tool_name=tool.group(1).strip(), tool_args=args)
    return ModelAction(kind="invalid", content="Your output protocol is invalid: model output must contain exactly one of <tool>, <tools>, or <final>", reasoning=reasoning, raw_content=text)
