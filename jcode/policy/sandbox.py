from __future__ import annotations


class SandboxPolicy:
    def __init__(self, mode: str):
        self.mode = mode

    def check_shell(self) -> tuple[bool, str]:
        if self.mode == "required":
            return False, "sandbox required but unavailable"
        if self.mode == "best_effort":
            return True, "sandbox unavailable; downgraded to direct execution"
        return True, "sandbox disabled"
