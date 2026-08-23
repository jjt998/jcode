from __future__ import annotations


def append_history(session: dict, role: str, content: str, **extra) -> None:
    session.setdefault("history", []).append({"role": role, "content": content, **extra})
