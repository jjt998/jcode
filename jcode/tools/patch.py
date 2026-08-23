from __future__ import annotations

from jcode.tools.base import ToolResult


def apply_text_patch(workspace, args) -> ToolResult:
    path = workspace.resolve_path(args.path)
    text = path.read_text(encoding="utf-8", errors="replace")
    count = text.count(args.old_text)
    if count != 1:
        return ToolResult("error", f"old_text matched {count} times; expected exactly 1", error_type="patch_nonunique")
    path.write_text(text.replace(args.old_text, args.new_text, 1), encoding="utf-8")
    return ToolResult("success", f"patched {workspace.relpath(path)}", changed_files=[workspace.relpath(path)])
