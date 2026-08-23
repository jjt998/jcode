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

    def check(self, tool) -> PermissionDecision:
        if self.approval == "never" and tool.risky:
            return PermissionDecision(False, "deny", "approval_denied")
        return PermissionDecision(True, "allow", "auto" if self.approval == "auto" else "ask_assumed")
