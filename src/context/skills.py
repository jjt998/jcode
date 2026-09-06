BUILTIN_CODING_SKILL = """Skill: coding-agent
Use tools when repository evidence is needed. Read before writing. Give the final answer only after the task is complete or clearly blocked.
"""


SKILL_ENTRIES = ({"name": "coding-agent", "content": BUILTIN_CODING_SKILL.strip(), "core": True, "source_order": 0},)


def select_skill_entries(level: int, entries=None) -> list[dict]:
    """按档位选择稳定顺序的技能条目，核心条目始终保留。"""
    values = list(entries or SKILL_ENTRIES)
    ratio = (100, 70, 70, 30, 70)[max(0, min(4, int(level)))]
    count = len(values) * ratio // 100
    selected = values[:max(count, sum(1 for item in values if item.get("core")))]
    return [dict(item) for item in selected]


def render_skill_section(entries=None) -> str:
    values = entries if entries is not None else SKILL_ENTRIES
    return "\n\n".join(str(item.get("content", "")).strip() for item in values if str(item.get("content", "")).strip())
