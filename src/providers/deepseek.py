from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from src.context.budget import estimate_tokens
from src.context.result import ContextResult, HistoryEvent
from src.providers.base import ModelResponse, ModelToolCall, ProviderRequestError
from src.providers.profiles import ModelProfile


class DeepSeekClient:
    """使用 DeepSeek Responses API 的原生工具调用适配器。"""

    def __init__(self, profile: ModelProfile):
        self.profile = profile
        self.api_key = profile.api_key
        self.base_url = profile.base_url.rstrip("/")
        self.model = profile.model

    def complete(self, context: ContextResult, *, model: str, max_tokens: int, temperature: float, model_profile: dict | None = None) -> ModelResponse:
        if not self.api_key:
            return ModelResponse(
                text="JCode is configured without an API key. The context was built but no provider request was sent.",
                finish_reason="missing_api_key",
                input_tokens=self._estimate_context_tokens(context),
            )
        payload = self._compile_request(context, model=model, max_tokens=max_tokens, temperature=temperature, model_profile=model_profile)
        request = urllib.request.Request(
            self.base_url + "/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ProviderRequestError(
                f"deepseek responses error {exc.code}: {body[:500]}",
                status_code=int(exc.code),
                retry_after_seconds=self._retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None),
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
            raise ProviderRequestError(f"deepseek transport error: {str(exc)[:500]}", transport_error=True) from exc
        return self._parse_response(data)

    def request_preview(self, context: ContextResult, *, model: str, max_tokens: int, temperature: float, model_profile: dict | None = None) -> dict:
        """返回不含认证信息的实际请求编译结果，供 Context 审计展示。"""
        return self._compile_request(
            context,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            model_profile=model_profile,
        )

    def _compile_request(self, context: ContextResult, *, model: str, max_tokens: int, temperature: float, model_profile: dict | None) -> dict:
        options = dict(model_profile or self.profile.snapshot())
        payload: dict[str, object] = {
            "model": model,
            "instructions": context.prefix,
            "input": self._compile_input(context),
            "tools": [
                {"type": "function", "name": tool.name, "description": tool.description, "parameters": tool.parameters}
                for tool in context.tools
            ],
            "max_output_tokens": max_tokens,
        }
        if bool(options.get("thinking_enabled", False)):
            payload["reasoning"] = {"effort": options["reasoning_effort"]}
        else:
            payload["temperature"] = temperature
        payload.update({key: value for key, value in self.profile.extra.items() if key not in {"thinking", "reasoning", "reasoning_effort"}})
        return payload

    def _compile_input(self, context: ContextResult) -> list[dict]:
        """按缓存友好顺序编译内部上下文、历史事件与当前请求。"""
        items: list[dict] = []
        if context.skill.strip():
            items.append({"role": "user", "content": context.skill})
        for event in context.history:
            items.extend(self._compile_history_event(event, continuation_run_id=str(context.provider_continuation.get("run_id") or "")))
        continuation_items = context.provider_continuation.get("items", [])
        if isinstance(continuation_items, list):
            items.extend(dict(item) for item in continuation_items if isinstance(item, dict))
        memory_text = context.working_memory.render().strip()
        if memory_text:
            items.append({"role": "user", "content": memory_text})
        items.append({"role": "user", "content": context.current_request})
        return items

    def _compile_history_event(self, event: HistoryEvent, *, continuation_run_id: str = "") -> list[dict]:
        if event.kind == "compact_summary":
            # 历史被永久压缩后，摘要必须先于保留回合提供给模型。
            return [{"role": "user", "content": "[JCode Compact Summary]\n" + event.content}]
        if event.kind == "user":
            return [{"role": "user", "content": event.content}]
        if event.kind == "assistant":
            return [{"role": "assistant", "content": event.content}]
        if event.kind == "tool_call":
            if continuation_run_id and event.turn_id == continuation_run_id:
                return []
            return [{"type": "function_call", "call_id": event.call_id, "name": event.tool_name, "arguments": json.dumps(event.arguments or {}, ensure_ascii=False)}]
        if event.kind == "tool_result":
            if continuation_run_id and event.turn_id == continuation_run_id:
                return []
            return [{"type": "function_call_output", "call_id": event.call_id, "output": event.content}]
        return []

    def _parse_response(self, data: dict) -> ModelResponse:
        output = [item for item in data.get("output", []) if isinstance(item, dict)]
        calls: list[ModelToolCall] = []
        reasoning_parts: list[str] = []
        text_parts: list[str] = []
        for item in output:
            item_type = str(item.get("type") or "")
            if item_type == "function_call":
                raw_arguments = item.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
                except (TypeError, ValueError, json.JSONDecodeError):
                    arguments = {}
                calls.append(ModelToolCall(str(item.get("call_id") or item.get("id") or ""), str(item.get("name") or ""), arguments, dict(item)))
            elif item_type == "reasoning":
                reasoning_parts.extend(self._content_text(item.get("summary") or item.get("content")))
            elif item_type == "message":
                text_parts.extend(self._content_text(item.get("content")))
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        incomplete_details = data.get("incomplete_details") if isinstance(data.get("incomplete_details"), dict) else {}
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        return ModelResponse(
            text=str(data.get("output_text") or "\n".join(text_parts)).strip(),
            reasoning="\n".join(reasoning_parts).strip(),
            tool_calls=calls,
            finish_reason=str(data.get("status") or "completed"),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            raw=data,
            incomplete_reason=str(incomplete_details.get("reason") or ""),
            provider_error_code=str(error.get("code") or error.get("type") or ""),
            provider_error_message=str(error.get("message") or ""),
        )

    @staticmethod
    def _retry_after_seconds(value: object) -> float | None:
        """仅接受正数秒数 Retry-After，日期格式交给默认退避策略处理。"""
        try:
            seconds = float(str(value or "").strip())
        except ValueError:
            return None
        return seconds if seconds > 0 else None

    @staticmethod
    def _content_text(content: object) -> list[str]:
        if isinstance(content, str):
            return [content]
        if not isinstance(content, list):
            return []
        return [str(item.get("text") or "") for item in content if isinstance(item, dict) and str(item.get("text") or "")]

    @staticmethod
    def _estimate_context_tokens(context: ContextResult) -> int:
        text = context.prefix + context.skill + context.current_request + context.working_memory.render()
        return estimate_tokens(text + "\n".join(event.content for event in context.history))
