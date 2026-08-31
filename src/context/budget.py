from __future__ import annotations


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def tail_clip(text: str, budget: int) -> str:
    if len(text) <= budget:
        return "History:\n: " + text
    return "History:\n...\n" + text[-max(0, budget - 4):]
