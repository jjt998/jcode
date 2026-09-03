BUILTIN_CODING_SKILL = """Skill: coding-agent
Use tools when repository evidence is needed. Read before writing. Give the final answer only after the task is complete or clearly blocked.
"""


def render_skill_section() -> str:
    return BUILTIN_CODING_SKILL.strip()
