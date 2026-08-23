from __future__ import annotations


def retrieve_into_working_memory(store, working_memory, query: str) -> list[str]:
    notes = store.retrieve(query)
    working_memory.retrieved_memory = notes
    return notes
