from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Iterable

from src.runtime.actions import parse_model_action


REASONING_RE = re.compile(r"<reasoning>(.*?)</reasoning>", re.DOTALL)


def build_reasoning_steps(events: Iterable[dict], *, run_id: str = "") -> tuple[list[dict], str]:
    """把 trace 事件聚合成步骤时间线。"""
    builder = StepTimelineBuilder(run_id=run_id)
    for event in events:
        builder.consume(event)
    return builder.steps_snapshot(), builder.final_text


class StepTimelineBuilder:
    """按模型响应和工具调用，持续拼出一个 turn 的步骤时间线。"""

    def __init__(self, run_id: str = ""):
        self.run_id = run_id
        self.steps: list[dict] = []
        self.current_step: dict | None = None
        self.pending_context_text = ""
        self.final_text = ""
        self._tool_seq = 0
        self._last_event_at = ""

    def consume(self, event: dict) -> list[dict]:
        name = str(event.get("event") or "message")
        created_at = str(event.get("created_at") or "")
        self._last_event_at = created_at or self._last_event_at
        patches: list[dict] = []

        if name == "context_built":
            self.pending_context_text = str(event.get("context") or "")
            if self.current_step is not None and not self.current_step.get("context_text"):
                self.current_step["context_text"] = self.pending_context_text
                self._push_detail(self.current_step, "context_built", "Context 拼凑", self.pending_context_text, event)
                patches.append(self._snapshot_step(self.current_step))
            return patches

        if name == "model_responded":
            self._finalize_current_step(success_if_open=True, end_at=created_at or self._last_event_at)
            action = parse_model_action(str(event.get("response_text") or ""))
            step = self._new_step(created_at)
            step["context_text"] = self.pending_context_text
            step["response_text"] = str(event.get("response_text") or "")
            step["reasoning_text"] = action.reasoning or _extract_reasoning(step["response_text"])
            step["parsed_action"] = _action_to_dict(action)
            step["status"] = "pending"
            step["error_text"] = ""
            self._push_detail(step, "model_responded", "模型原始返回", step["response_text"], event)
            self._push_detail(step, "model_parsed", "模型解析结果", json.dumps(step["parsed_action"], ensure_ascii=False, indent=2), event)
            self.current_step = step
            self.pending_context_text = ""
            self.steps.append(step)
            patches.append(self._snapshot_step(step))
            return patches

        step = self._ensure_step(created_at)
        if step is None:
            return patches

        if name == "model_parsed":
            step["parsed_action"] = _action_to_dict(event.get("action") or {})
            self._push_detail(step, name, "模型解析结果", json.dumps(event.get("action") or {}, ensure_ascii=False, indent=2), event)
            patches.append(self._snapshot_step(step))
            return patches

        if name == "model_parse_failed":
            step["status"] = "error"
            if not step.get("error_text"):
                step["error_text"] = str(event.get("error") or "")
            content = json.dumps(
                {
                    "error": event.get("error") or "",
                    "raw_content": event.get("raw_content") or "",
                    "reasoning": event.get("reasoning") or "",
                },
                ensure_ascii=False,
                indent=2,
            )
            self._push_detail(step, name, "模型解析失败", content, event)
            patches.append(self._snapshot_step(step))
            return patches

        if name in {"tool_requested", "tool_executed", "subagent_completed"}:
            self._update_tool_call(step, event)
            if name == "tool_requested":
                step["status"] = "running"
            else:
                tool_status = _normalize_status(str(event.get("status") or ""), default="success")
                step["status"] = tool_status
                if tool_status in {"error", "timeout"} and not step.get("error_text"):
                    step["error_text"] = _step_error_text(event)
            self._push_detail(step, name, _event_title(name, event), _event_content(name, event), event)
            patches.append(self._snapshot_step(step))
            return patches

        if name in {"tool_sequence_requested", "tool_sequence_step_requested", "tool_sequence_completed", "tool_sequence_aborted"}:
            self._push_detail(step, name, _event_title(name, event), _event_content(name, event), event)
            if name == "tool_sequence_requested" and not step.get("reasoning_text"):
                step["status"] = "running"
            if name == "tool_sequence_completed" and step.get("status") not in {"error", "timeout"}:
                step["status"] = "success"
            if name == "tool_sequence_aborted":
                step["status"] = "error"
                if not step.get("error_text"):
                    step["error_text"] = _step_error_text(event)
            patches.append(self._snapshot_step(step))
            return patches

        if name in {"checkpoint_created", "final_readiness_decision", "memory_maintained", "run_finished"}:
            if self.current_step is None:
                if name == "run_finished":
                    self.final_text = str(event.get("final_text") or self.final_text)
                return patches
            self._push_detail(step, name, _event_title(name, event), _event_content(name, event), event)
            if name == "run_finished" and step.get("status") not in {"error", "timeout"}:
                step["status"] = "success"
            patches.append(self._snapshot_step(step))
            if name == "run_finished":
                self._finalize_current_step(success_if_open=True, end_at=created_at or self._last_event_at)
            return patches

        if name == "web_run_completed":
            self.final_text = str(event.get("final_text") or "")
            if self.current_step is None:
                return patches
            if step.get("status") not in {"error", "timeout"}:
                step["status"] = "success"
            patches.append(self._snapshot_step(step))
            self._finalize_current_step(success_if_open=True, end_at=created_at or self._last_event_at)
            return patches

        if name in {"approval_required", "approval_answered"}:
            self._push_detail(step, name, _event_title(name, event), _event_content(name, event), event)
            patches.append(self._snapshot_step(step))
            return patches

        if name == "run_failed":
            step["status"] = "error"
            step["error_text"] = _step_error_text(event)
            self._push_detail(step, name, _event_title(name, event), _event_content(name, event), event)
            patches.append(self._snapshot_step(step))
            self._finalize_current_step(success_if_open=False, end_at=created_at or self._last_event_at)
            return patches

        if name == "run_aborted":
            step["status"] = "timeout"
            if not step.get("error_text"):
                step["error_text"] = _step_error_text(event)
            self._push_detail(step, name, _event_title(name, event), _event_content(name, event), event)
            patches.append(self._snapshot_step(step))
            self._finalize_current_step(success_if_open=False, end_at=created_at or self._last_event_at)
            return patches

        self._push_detail(step, name, _event_title(name, event), _event_content(name, event), event)
        patches.append(self._snapshot_step(step))
        return patches

    def steps_snapshot(self) -> list[dict]:
        return [self._snapshot_step(step) for step in self.steps]

    def _ensure_step(self, created_at: str) -> dict | None:
        if self.current_step is not None:
            return self.current_step
        return None

    def _new_step(self, created_at: str) -> dict:
        index = len(self.steps) + 1
        step_id = f"{self.run_id}:{index}" if self.run_id else f"step-{index}"
        return {
            "step_id": step_id,
            "index": index,
            "timestamp": created_at,
            "end_timestamp": "",
            "status": "pending",
            "reasoning_text": "",
            "reasoning_summary": "",
            "context_text": "",
            "response_text": "",
            "error_text": "",
            "parsed_action": {},
            "tool_calls": [],
            "details": [],
            "_start_at": created_at,
            "_last_event_at": created_at,
        }

    def _push_detail(self, step: dict, event_name: str, title: str, content: str, event: dict) -> None:
        step["details"].append(
            {
                "event": event_name,
                "title": title,
                "content": content,
                "created_at": str(event.get("created_at") or ""),
            }
        )

    def _update_tool_call(self, step: dict, event: dict) -> None:
        name = str(event.get("name") or event.get("tool_name") or "").strip()
        args = event.get("args")
        result = event.get("result")
        status = str(event.get("status") or "").strip()
        now = str(event.get("created_at") or "")

        if event.get("event") == "tool_requested":
            self._tool_seq += 1
            tool_call = {
                "tool_id": f"{step['step_id']}:tool-{self._tool_seq}",
                "name": name,
                "args_text": _stringify_payload(args),
                "status": "running",
                "duration_ms": None,
                "result_text": "",
                "error_type": "",
                "started_at": now,
                "finished_at": "",
            }
            step["tool_calls"].append(tool_call)
            step["_last_event_at"] = now or step["_last_event_at"]
            return

        tool_call = None
        for item in reversed(step["tool_calls"]):
            if item.get("status") == "running" and not item.get("result_text"):
                tool_call = item
                break
        if tool_call is None:
            self._tool_seq += 1
            tool_call = {
                "tool_id": f"{step['step_id']}:tool-{self._tool_seq}",
                "name": name,
                "args_text": _stringify_payload(args),
                "status": "running",
                "duration_ms": None,
                "result_text": "",
                "error_type": "",
                "started_at": "",
                "finished_at": "",
            }
            step["tool_calls"].append(tool_call)

        tool_call["name"] = tool_call["name"] or name
        if not tool_call["args_text"]:
            tool_call["args_text"] = _stringify_payload(args)
        tool_call["status"] = _normalize_status(status, default="success")
        tool_call["result_text"] = _stringify_payload(result if result is not None else event.get("result_text") or event.get("content") or "")
        tool_call["error_type"] = str(event.get("error_type") or "")
        tool_call["finished_at"] = now
        tool_call["duration_ms"] = _duration_ms(tool_call.get("started_at", ""), now)
        step["_last_event_at"] = now or step["_last_event_at"]

    def _finalize_current_step(self, *, success_if_open: bool, end_at: str) -> None:
        if self.current_step is None:
            return
        self.current_step["end_timestamp"] = end_at or self.current_step.get("end_timestamp") or self.current_step.get("_last_event_at") or self.current_step.get("_start_at") or ""
        if success_if_open and self.current_step.get("status") == "pending":
            self.current_step["status"] = "success"
        self.current_step["reasoning_summary"] = _summarize(self.current_step.get("reasoning_text", ""))
        self.current_step = None

    def _snapshot_step(self, step: dict) -> dict:
        data = deepcopy(step)
        start_at = data.pop("_start_at", "")
        last_at = data.pop("_last_event_at", "")
        if not data.get("end_timestamp"):
            data["end_timestamp"] = last_at or start_at
        data["duration_ms"] = _duration_ms(start_at, data.get("end_timestamp") or last_at)
        data["tool_count"] = len(data.get("tool_calls", []))
        data["reasoning_summary"] = _summarize(data.get("reasoning_text", ""))
        return data


def _event_title(name: str, event: dict) -> str:
    tool = event.get("name") or event.get("tool_name")
    labels = {
        "context_built": "Context 拼凑",
        "model_responded": "模型原始返回",
        "model_parsed": "模型解析结果",
        "model_parse_failed": "模型解析失败",
        "tool_requested": f"工具请求{f': {tool}' if tool else ''}",
        "tool_executed": f"工具结果{f': {tool}' if tool else ''}",
        "subagent_completed": f"子任务结果{f': {tool}' if tool else ''}",
        "tool_sequence_requested": "工具序列请求",
        "tool_sequence_step_requested": "工具序列步骤",
        "tool_sequence_completed": "工具序列完成",
        "tool_sequence_aborted": "工具序列中止",
        "checkpoint_created": "Checkpoint",
        "final_readiness_decision": "Final gate",
        "memory_maintained": "记忆整理",
        "run_finished": "运行结束",
        "approval_required": "等待确认",
        "approval_answered": "已确认",
        "web_run_completed": "最终答案",
        "run_failed": "失败",
        "run_aborted": "已停止",
    }
    return labels.get(name, name)


def _event_content(name: str, event: dict) -> str:
    if name == "context_built":
        return str(event.get("context") or "")
    if name == "model_responded":
        return str(event.get("response_text") or "")
    if name == "model_parsed":
        return json.dumps(event.get("action") or {}, ensure_ascii=False, indent=2)
    if name == "model_parse_failed":
        return json.dumps(
            {
                "error": event.get("error") or "",
                "raw_content": event.get("raw_content") or "",
                "reasoning": event.get("reasoning") or "",
            },
            ensure_ascii=False,
            indent=2,
        )
    if name in {"tool_requested", "tool_executed", "subagent_completed"}:
        if name == "tool_requested":
            return _stringify_payload(event.get("args") or {})
        return _stringify_payload(event.get("result") or event.get("content") or "")
    if name == "final_readiness_decision":
        return json.dumps({k: v for k, v in event.items() if k not in {"event", "created_at", "run_id"}}, ensure_ascii=False, indent=2)
    if name in {"checkpoint_created", "tool_sequence_requested", "tool_sequence_step_requested", "tool_sequence_completed", "tool_sequence_aborted", "memory_maintained", "run_finished", "approval_required", "approval_answered", "web_run_completed", "run_failed", "run_aborted"}:
        return json.dumps({k: v for k, v in event.items() if k not in {"event", "created_at", "run_id"}}, ensure_ascii=False, indent=2)
    return json.dumps({k: v for k, v in event.items() if k not in {"event", "created_at", "run_id"}}, ensure_ascii=False, indent=2)


def _extract_reasoning(text: str) -> str:
    match = REASONING_RE.search(text)
    if not match:
        return ""
    return match.group(1).strip()


def _summarize(text: str, limit: int = 20) -> str:
    value = str(text or "").strip().replace("\n", " ")
    if not value:
        return ""
    return value[:limit] + ("…" if len(value) > limit else "")


def _stringify_payload(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)


def _step_error_text(event: dict) -> str:
    text = _stringify_payload(event.get("error") or event.get("result") or event.get("content") or event.get("result_text") or "")
    if text:
        return text
    error_type = str(event.get("error_type") or "").strip()
    if error_type:
        return error_type
    return _stringify_payload({k: v for k, v in event.items() if k not in {"event", "created_at", "run_id"}})


def _duration_ms(start_at: str, end_at: str) -> int | None:
    start = _parse_time(start_at)
    end = _parse_time(end_at)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _action_to_dict(action) -> dict:
    if isinstance(action, dict):
        return dict(action)
    if getattr(action, "kind", "") == "tools":
        return {
            "kind": action.kind,
            "content": action.content,
            "reasoning": action.reasoning,
            "tool_calls": [{"name": call.name, "args": call.args} for call in getattr(action, "tool_calls", [])],
        }
    return {
        "kind": getattr(action, "kind", ""),
        "content": getattr(action, "content", ""),
        "reasoning": getattr(action, "reasoning", ""),
        "tool_name": getattr(action, "tool_name", ""),
        "tool_args": getattr(action, "tool_args", {}),
    }


def _normalize_status(value: str, *, default: str = "success") -> str:
    status = str(value or "").strip().lower()
    if status in {"success", "running", "pending"}:
        return "success" if status == "success" else status
    if status in {"error", "timeout"}:
        return status
    if not status:
        return default
    return "error"
