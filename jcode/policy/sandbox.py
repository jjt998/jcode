from __future__ import annotations

from jcode.policy.decisions import PolicyDecision


class SandboxPolicy:
    mode: str

    def __init__(self, mode: str):
        self.mode = mode

    def check_shell(self) -> tuple[bool, str]:
        if self.mode == "required":
            return False, "sandbox required but unavailable"
        if self.mode == "best_effort":
            return True, "sandbox unavailable; downgraded to direct execution"
        return True, "sandbox disabled"

    def decide_shell(self) -> PolicyDecision:
        ok, message = self.check_shell()
        if ok:
            return PolicyDecision.allow("sandbox_ok", layer="sandbox", risk="medium", metadata={"mode": self.mode, "message": message})
        return PolicyDecision.deny("sandbox_required", f"error: {message}", layer="sandbox", metadata={"mode": self.mode})
