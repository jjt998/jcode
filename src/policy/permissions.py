from __future__ import annotations

from dataclasses import dataclass

from src.policy.decisions import PolicyDecision


@dataclass
class PermissionDecision:
    allowed: bool
    decision: str
    reason: str = ""


class PermissionChecker:
    approval: str

    def __init__(self, approval: str):
        self.approval = approval

    def check(self, tool, args: dict | None = None) -> PermissionDecision:
        if self.approval == "never" and tool.risky:
            return PermissionDecision(False, "deny", "approval_denied")
        if self.approval == "ask" and tool.risky:
            return PermissionDecision(True, "allow", "ask_assumed")
        return PermissionDecision(True, "allow", "auto" if self.approval == "auto" else "ask_assumed")

    def decide(self, tool, args: dict | None = None) -> PolicyDecision:
        decision = self.check(tool, args)
        if decision.allowed:
            return PolicyDecision.allow(decision.reason, layer="permission", risk="medium" if tool.risky else "low")
        return PolicyDecision.deny(decision.reason, f"error: permission denied for {tool.name}: {decision.reason}", layer="permission")
