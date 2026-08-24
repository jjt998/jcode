from __future__ import annotations

SECRET_HINTS = ("api_key", "token", "password", "secret")


def looks_sensitive(text: str) -> bool:
    return any(hint in text.lower() for hint in SECRET_HINTS)
