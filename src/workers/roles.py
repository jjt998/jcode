from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubagentRoleSpec:
    role_id: str  # 角色协议 ID
    display_name: str  # 角色展示名称
    system_instruction: str  # 角色专属系统约束
    tool_profile: str  # 绑定的工具集合
    allow_write: bool  # 是否允许直接写入文件
    allow_shell: bool  # 是否允许执行 shell
    allow_nested_spawn: bool  # 是否允许继续创建子 Agent
    output_contract: str  # 角色结果契约


ROLE_SPECS: dict[str, SubagentRoleSpec] = {
    "explorer": SubagentRoleSpec(
        "explorer",
        "Explorer",
        "只调查代码、文档和依赖，必须引用实际文件证据，不修改工作区。",
        "readonly",
        False,
        False,
        False,
        "输出事实、证据路径和未知项。",
    ),
    "planner": SubagentRoleSpec(
        "planner",
        "Planner",
        "只拆解任务、依赖和验收条件，不修改工作区，也不进入 plan mode。",
        "readonly",
        False,
        False,
        False,
        "输出有序步骤、依赖关系和可检查验收条件。",
    ),
    "worker": SubagentRoleSpec(
        "worker",
        "Worker",
        "在明确 write_scope 内完成代码或文档修改；完成前必须检查实际文件结果。",
        "worker",
        True,
        False,
        False,
        "输出修改摘要、变更文件和未完成项。",
    ),
    "tester": SubagentRoleSpec(
        "tester",
        "Tester",
        "只执行验证命令和读取结果，禁止直接写文件；必须报告 shell 产生的所有副作用。",
        "tester",
        False,
        True,
        False,
        "输出命令、返回码、验证结论和副作用。",
    ),
    "reviewer": SubagentRoleSpec(
        "reviewer",
        "Reviewer",
        "只审查当前工作区实现，按严重级别报告问题和证据，不修改工作区。",
        "readonly",
        False,
        False,
        False,
        "输出按严重级别排列的审查发现和结论。",
    ),
}


def get_role_spec(role: str) -> SubagentRoleSpec:
    """解析固定角色；未知角色必须直接失败。"""
    key = str(role or "").strip()
    try:
        return ROLE_SPECS[key]
    except KeyError as exc:
        raise ValueError(f"invalid_subagent_role: {role}") from exc


def plan_mode_allowed_roles() -> frozenset[str]:
    """plan mode 只允许无写入、无 shell 的只读角色。"""
    return frozenset({"explorer", "planner", "reviewer"})
