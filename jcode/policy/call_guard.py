from __future__ import annotations

import json


class CallGuard:
    def __init__(self):
        self.seen: set[str] = set()

    def repeated(self, name: str, args: dict) -> bool:
        key = name + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False)
        if key in self.seen:
            return True
        self.seen.add(key)
        return False
