from __future__ import annotations


def retrieve_into_working_memory(store, working_memory, query: str) -> list[dict]:
    """每个新请求都从 active 长期记忆执行一次可解释词法召回。"""
    hits = store.retrieve(query)
    working_memory.set_retrieval(query, hits)
    return hits
