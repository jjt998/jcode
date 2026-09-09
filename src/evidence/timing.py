from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

from src.state.workspace import now_iso


@dataclass
class TimingSpan:
    """记录一个 Harness 组件的单次墙钟耗时。"""

    component: str  # Harness 组件名称
    operation: str  # 组件内操作名称
    span_id: str  # 单次计时标识
    parent_span_id: str  # 父计时标识
    level: str  # phase 为一级阶段，detail 为内部明细
    started_at: str  # 墙钟开始时间
    started_ns: int  # 单调时钟开始值
    metadata: dict = field(default_factory=dict)  # 不含业务正文的辅助信息


class HarnessTiming:
    """统一收集 Harness 组件耗时，并把摘要打印到控制台。"""

    def __init__(self, run_id: str = ""):
        self.run_id = run_id
        self._sequence = 0
        self.spans: list[dict] = []

    def start(self, component: str, operation: str, *, parent_span_id: str = "", level: str = "phase", metadata: dict | None = None) -> TimingSpan:
        self._sequence += 1
        span_id = f"{self.run_id}:{component}:{self._sequence}"
        return TimingSpan(
            component=component,
            operation=operation,
            span_id=span_id,
            parent_span_id=parent_span_id,
            level=level,
            started_at=now_iso(),
            started_ns=time.perf_counter_ns(),
            metadata=dict(metadata or {}),
        )

    def finish(self, span: TimingSpan, *, status: str = "success", metadata: dict | None = None) -> dict:
        finished_at = now_iso()
        record = {
            "event": "harness_component_finished",
            "run_id": self.run_id,
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "level": span.level,
            "component": span.component,
            "operation": span.operation,
            "started_at": span.started_at,
            "finished_at": finished_at,
            "duration_ms": max(0, int((time.perf_counter_ns() - span.started_ns) / 1_000_000)),
            "status": status,
            "metadata": {**span.metadata, **dict(metadata or {})},
        }
        self.spans.append(record)
        print(
            f"[harness-timing] run={self.run_id} component={span.component} "
            f"operation={span.operation} level={span.level} duration={record['duration_ms']}ms status={status}",
            file=sys.stderr,
            flush=True,
        )
        return record

    def summary(self, duration_ms: int | None = None, *, status: str = "completed", print_total: bool = False) -> dict:
        phases: dict[str, int] = {}
        details: dict[str, int] = {}
        for span in self.spans:
            component = str(span["component"])
            target = phases if span.get("level") == "phase" else details
            target[component] = target.get(component, 0) + int(span["duration_ms"])
        measured_phase_ms = sum(phases.values())
        summary = {
            "duration_ms": duration_ms,
            "components": phases,
            "details": details,
            "measured_phase_ms": measured_phase_ms,
            "unaccounted_ms": None if duration_ms is None else max(0, duration_ms - measured_phase_ms),
            "span_count": len(self.spans),
            "spans": list(self.spans),
        }
        if duration_ms is not None and print_total:
            print(f"[harness-timing] run={self.run_id} component=harness_run operation=total duration={duration_ms}ms status={status}", file=sys.stderr, flush=True)
        return summary
