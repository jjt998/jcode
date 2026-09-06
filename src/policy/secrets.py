from __future__ import annotations

import os
from collections.abc import Mapping, Sequence


class SecretRedactor:
    values: set[str]

    def __init__(self, values: set[str]):
        self.values = {value for value in values if value}

    @classmethod
    def from_environment(cls, extra_names=()):
        names = {"JCODE_API_KEY", "OPENAI_API_KEY", *extra_names}
        return cls({os.environ.get(name, "") for name in names})

    def redact(self, text: str) -> str:
        redacted = str(text)
        for value in self.values:
            redacted = redacted.replace(value, "[REDACTED]")
        return redacted

    def redact_value(self, value):
        """递归脱敏嵌套参数，保持列表和字典结构不变。"""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, Mapping):
            return {str(key): self.redact_value(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return [self.redact_value(item) for item in value]
        return value
