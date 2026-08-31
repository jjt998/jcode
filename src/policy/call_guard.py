from __future__ import annotations

import json


class CallGuard:
    counts: dict[str, int]

    def __init__(self):
        self.counts = {}

    def repeated(self, name: str, args: dict, *, context_key: str = "") -> bool:
        key = name + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False) + ":" + str(context_key)
        if self.counts.get(key, 0) >= 3:
            return True
        self.counts[key] = self.counts.get(key, 0) + 1
        return False
