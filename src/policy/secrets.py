from __future__ import annotations

import os


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
