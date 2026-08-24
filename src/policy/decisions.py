from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    reason: str
    message: str = ""
    layer: str = ""
    risk: str = "low"
    metadata: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision in {"allow", "warn"}

    @classmethod
    def allow(cls, reason: str = "ok", *, layer: str = "", risk: str = "low", metadata: dict | None = None) -> "PolicyDecision":
        return cls("allow", reason, layer=layer, risk=risk, metadata=dict(metadata or {}))

    @classmethod
    def warn(cls, reason: str, message: str, *, layer: str = "", risk: str = "medium", metadata: dict | None = None) -> "PolicyDecision":
        return cls("warn", reason, message, layer=layer, risk=risk, metadata=dict(metadata or {}))

    @classmethod
    def deny(cls, reason: str, message: str, *, layer: str = "", risk: str = "high", metadata: dict | None = None) -> "PolicyDecision":
        return cls("deny", reason, message, layer=layer, risk=risk, metadata=dict(metadata or {}))

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "message": self.message,
            "layer": self.layer,
            "risk": self.risk,
            "metadata": dict(self.metadata),
        }
