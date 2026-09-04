from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderContinuation:
    """保存同一 run 内必须回传给 Provider 的原生调用链。"""

    run_id: str = ""  # 归属的运行标识
    items: list[dict] = field(default_factory=list)  # reasoning、function_call 与 function_call_output 原生项

    @classmethod
    def from_dict(cls, data: object, *, run_id: str = "") -> "ProviderContinuation":
        if not isinstance(data, dict):
            return cls(run_id=run_id)
        items = data.get("items", [])
        return cls(
            run_id=str(data.get("run_id") or run_id),
            items=[dict(item) for item in items if isinstance(item, dict)],
        )

    def add_response_items(self, output: object) -> None:
        """记录需要在工具调用后续请求中原样回传的模型原生项。"""
        if not isinstance(output, list):
            return
        for item in output:
            if not isinstance(item, dict) or item.get("type") not in {"reasoning", "function_call"}:
                continue
            self.items.append(dict(item))

    def add_tool_output(self, call_id: str, output: str) -> None:
        """按原调用标识追加工具执行结果，保持 Responses 调用闭环。"""
        self.items.append(
            {
                "type": "function_call_output",
                "call_id": str(call_id),
                "output": str(output),
            }
        )

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "items": [dict(item) for item in self.items]}
