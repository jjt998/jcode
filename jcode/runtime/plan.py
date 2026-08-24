from __future__ import annotations

import re


_PLAN_DIR_MARKER = "/.jcode/plans/"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "plan"


def plan_path_for(topic: str, path: str | None = None) -> str:
    if path:
        value = str(path).strip()
        if value.startswith("/") and _PLAN_DIR_MARKER in value:
            value = value[value.index(_PLAN_DIR_MARKER) + 1 :]
        if value.startswith("./"):
            value = value[2:]
    else:
        value = f".jcode/plans/{slugify(topic)}-plan.md"
    if not value.startswith(".jcode/plans/") or value.endswith("/") or ".." in value.split("/"):
        raise ValueError("plan path must stay under .jcode/plans/")
    return value


def runtime_mode_state(session: dict) -> dict:
    state = session.get("runtime_mode", {})
    return dict(state) if isinstance(state, dict) else {"mode": "default"}


def runtime_mode_name(session: dict) -> str:
    return str(runtime_mode_state(session).get("mode", "default") or "default")


def runtime_mode_plan_path(session: dict) -> str:
    return str(runtime_mode_state(session).get("plan_path", "") or "")


def render_runtime_mode_text(session: dict) -> str:
    if runtime_mode_name(session) != "plan":
        return ""
    plan_path = runtime_mode_plan_path(session)
    topic = str(runtime_mode_state(session).get("topic", "") or "")
    lines = [
        "Runtime mode: plan",
        f"- topic: {topic or '-'}",
        f"- active plan artifact: {plan_path or '-'}",
        "- You may inspect files, but writes must target only the active plan artifact.",
        "- You may launch Explore subagents, but not write-capable worker subagents.",
        "- Return a final answer only after the active plan artifact has been written.",
    ]
    return "\n".join(lines)


def plan_artifact_ready(session: dict, workspace, plan_path: str | None = None) -> bool:
    mode = runtime_mode_name(session)
    if mode != "plan":
        return True
    raw_path = plan_path or runtime_mode_plan_path(session)
    if not raw_path:
        return False
    try:
        path = workspace.resolve_path(raw_path)
    except Exception:
        return False
    try:
        return path.is_file() and bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


class PlanModeController:
    runtime: object

    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def state(self) -> dict:
        state = self.runtime.session.setdefault("runtime_mode", {"mode": "default"})
        return state if isinstance(state, dict) else {"mode": "default"}

    @property
    def mode(self) -> str:
        return runtime_mode_name(self.runtime.session)

    @property
    def plan_path(self) -> str:
        return runtime_mode_plan_path(self.runtime.session)

    def enter(self, topic: str, path: str | None = None) -> str:
        plan_path = plan_path_for(topic, path)
        self.runtime.session["runtime_mode"] = {
            "mode": "plan",
            "topic": str(topic or ""),
            "plan_path": plan_path,
        }
        self.runtime.set_tool_profile("plan")
        self.runtime.write_scope = [plan_path]
        self.runtime.session_path = self.runtime.session_store.save(self.runtime.session)
        self.runtime.refresh_prefix(force=True)
        self.runtime.session_events.emit(
            "runtime_mode_changed",
            mode="plan",
            plan_path=plan_path,
            topic=str(topic or ""),
        )
        return plan_path

    def exit(self) -> None:
        previous = dict(self.state)
        self.runtime.session["runtime_mode"] = {"mode": "default"}
        self.runtime.set_tool_profile("default")
        self.runtime.write_scope = []
        self.runtime.session_path = self.runtime.session_store.save(self.runtime.session)
        self.runtime.refresh_prefix(force=True)
        self.runtime.session_events.emit(
            "runtime_mode_changed",
            mode="default",
            previous_mode=previous.get("mode", "default"),
            plan_path=previous.get("plan_path", ""),
        )

    def can_finish(self) -> bool:
        return plan_artifact_ready(self.runtime.session, self.runtime.workspace)

    def final_notice(self) -> str:
        return f"Plan mode requires writing the active plan artifact before final answer: {self.plan_path}"
