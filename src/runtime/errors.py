from __future__ import annotations


class JCodeRuntimeStopError(RuntimeError):
    """可安全展示给用户的运行时停止错误。"""
    code = "runtime_stop"
    user_message = "当前运行已停止。"

    def __init__(self, message: str | None = None, *, audit: dict | None = None):
        super().__init__(message or self.user_message)
        self.audit = dict(audit or {})


class MandatoryContextExceedsWindowError(JCodeRuntimeStopError):
    code = "mandatory_context_exceeds_window"
    user_message = "当前已无剩余上下文空间，请你另开会话吧。"


class SystemMinimumBudgetOverflowError(JCodeRuntimeStopError):
    code = "system_minimum_budget_overflow"
    user_message = "系统最低上下文预算无法满足，请你另开会话吧。"


class FinalContextExceedsWindowError(JCodeRuntimeStopError):
    code = "final_context_exceeds_window"
    user_message = "当前已无剩余上下文空间，请你另开会话吧。"


class UnsupportedProviderContinuationItemError(JCodeRuntimeStopError):
    code = "unsupported_provider_continuation_item"
    user_message = "Provider 续接数据包含不支持的原生项。"


class ArtifactWriteFailureError(JCodeRuntimeStopError):
    code = "artifact_write_failure"
    user_message = "artifact写入失败，当前运行已停止。"


class ArtifactUnavailableError(JCodeRuntimeStopError):
    code = "artifact_unavailable"
    user_message = "所需 artifact 不可用，当前运行已停止。"


class ArtifactIntegrityError(JCodeRuntimeStopError):
    code = "artifact_integrity_error"
    user_message = "artifact完整性校验失败，当前运行已停止。"


class FreshnessRefreshError(JCodeRuntimeStopError):
    code = "freshness_refresh_error"
    user_message = "文件 freshness 刷新失败，当前运行已停止。"
