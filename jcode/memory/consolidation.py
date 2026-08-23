from __future__ import annotations


def maintain_after_turn(store, working_memory, user_message: str, final_text: str) -> dict:
    promoted = store.promote_from_turn(user_message, final_text)
    if promoted:
        working_memory.durable_promotions.extend(promoted)
    return {"promoted_count": len(promoted), "promoted_preview": [text[:200] for text in promoted]}
