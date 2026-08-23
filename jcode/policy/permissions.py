from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PermissionDecision:
    allowed: bool
    decision: str
    reason: str = ""


class PermissionChecker:
    def __init__(self, approval: str):
        self.approval = approval

    def check(self, tool, args: dict | None = None) -> PermissionDecision:
        if self.approval == "never" and tool.risky:
            return PermissionDecision(False, "deny", "approval_denied")
        if self.approval == "ask" and tool.risky:
            return PermissionDecision(True, "allow", "ask_assumed")
        return PermissionDecision(True, "allow", "auto" if self.approval == "auto" else "ask_assumed")
