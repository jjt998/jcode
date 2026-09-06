from __future__ import annotations

import json
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.context.budget import SAFETY_MARGIN, TokenizerAdapter, effective_window


class CompactSummaryPayload(BaseModel):
    """Level 4 摘要固定 JSON schema。"""
    model_config = ConfigDict(extra="forbid")
    summary_version: str = "9.5"
    task_goal: str = ""
    decisions: list[dict] = Field(default_factory=list)
    files_read: list[dict] = Field(default_factory=list)
    files_modified: list[dict] = Field(default_factory=list)
    key_findings: list[dict] = Field(default_factory=list)
    tool_failures: list[dict] = Field(default_factory=list)
    freshness_events: list[dict] = Field(default_factory=list)
    unresolved_blockers: list[dict] = Field(default_factory=list)
    next_steps: list[dict] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)


def build_summary_request(instructions: str, old_summary: dict | None, evicted_turns: list[dict]) -> dict:
    """摘要请求只携带摘要指令、旧摘要和本次淘汰事件。"""
    return {"instructions": str(instructions), "input": [{"role": "user", "content": json.dumps({"previous_summary": old_summary or {}, "evicted_turns": evicted_turns}, ensure_ascii=False, separators=(",", ":"))}], "tools": []}


def validate_summary_payload(value: Any) -> CompactSummaryPayload:
    if isinstance(value, str):
        value = json.loads(value)
    payload = CompactSummaryPayload.model_validate(value)
    for field_name in ("decisions", "files_read", "files_modified", "key_findings", "tool_failures", "freshness_events", "unresolved_blockers", "next_steps"):
        values = getattr(payload, field_name)
        if any(not isinstance(item, dict) or len(json.dumps(item, ensure_ascii=False)) > 1000 for item in values):
            raise ValueError(f"summary field {field_name} contains an invalid long item")
    for path in payload.artifact_paths:
        if not isinstance(path, str) or not path.strip() or "\\" in path or path.startswith("/"):
            raise ValueError("artifact_paths must contain workspace-relative paths")
    return payload


def build_deterministic_summary(old_summary: dict | None, evicted_turns: list[dict], *, task_goal: str = "") -> CompactSummaryPayload:
    """仅聚合结构化事件，不读取 assistant 正文推断事实。"""
    base = dict(old_summary or {})
    data = {key: base.get(key, []) for key in ("decisions", "files_read", "files_modified", "key_findings", "tool_failures", "freshness_events", "unresolved_blockers", "next_steps")}
    data.update({"summary_version": str(base.get("summary_version", "9.5")), "task_goal": task_goal or str(base.get("task_goal", "")), "artifact_paths": list(base.get("artifact_paths", []))})
    for event in evicted_turns:
        if not isinstance(event, dict):
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        tool_name = event.get("tool_name") or event.get("name")
        if tool_name in {"read_file", "write_file", "apply_patch"}:
            path = str((event.get("arguments") or event.get("args") or {}).get("path", "")).strip()
            if path:
                target = "files_read" if tool_name == "read_file" else "files_modified"
                data[target].append({"path": path, "tool": tool_name})
        if event.get("tool_status") and event.get("tool_status") != "success":
            data["tool_failures"].append({"tool": str(tool_name or ""), "status": str(event.get("tool_status")), "code": metadata.get("return_code", "")})
        if metadata.get("freshness_event"):
            data["freshness_events"].append(dict(metadata["freshness_event"]))
        for path in metadata.get("artifact_paths", []) if isinstance(metadata.get("artifact_paths"), list) else []:
            if str(path) not in data["artifact_paths"]:
                data["artifact_paths"].append(str(path))
    for key in data:
        if isinstance(data[key], list):
            seen = set(); data[key] = [item for item in data[key] if not (json.dumps(item, ensure_ascii=False, sort_keys=True) in seen or seen.add(json.dumps(item, ensure_ascii=False, sort_keys=True)))]
    return validate_summary_payload(data)


def summary_output_reservation(profile, actual_max_new_tokens: int, summary_input_tokens: int) -> int:
    available = effective_window(profile.context_window_tokens) - int(summary_input_tokens) - SAFETY_MARGIN
    return max(0, min(int(actual_max_new_tokens), available))


def call_summary_model(router, request: dict, *, profile_id: str, profile, actual_max_new_tokens: int, tokenizer: TokenizerAdapter | None = None, timeout_seconds: int = 120, retry_count: int = 2, initial_retry_delay_seconds: int = 2, retry_multiplier: int = 2) -> tuple[CompactSummaryPayload | None, dict]:
    """调用摘要模型，固定最多三次失败后返回确定性 fallback 所需审计。"""
    adapter = tokenizer or TokenizerAdapter()
    input_tokens = adapter.count(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
    reservation = summary_output_reservation(profile, actual_max_new_tokens, input_tokens)
    audit = {"attempts": 0, "input_tokens": input_tokens, "output_reservation": reservation, "failures": []}
    if reservation < 2048:
        audit["fallback_reason"] = "summary_output_below_minimum"
        return None, audit
    for attempt in range(min(3, int(retry_count) + 1)):
        audit["attempts"] += 1
        try:
            raw = router.complete_summary(summary_provider_input=request, profile_id=profile_id, max_output_tokens=reservation, timeout_seconds=timeout_seconds)
            payload = validate_summary_payload(raw)
            audit["status"] = "success"
            return payload, audit
        except Exception as exc:
            audit["failures"].append({"attempt": attempt + 1, "error_type": type(exc).__name__, "message": str(exc)[:500]})
            if attempt + 1 < min(3, int(retry_count) + 1):
                time.sleep(max(0, int(initial_retry_delay_seconds)) * (int(retry_multiplier) ** attempt))
    audit["status"] = "fallback"
    audit["fallback_reason"] = "summary_model_failed"
    return None, audit
