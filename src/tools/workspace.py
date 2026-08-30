from __future__ import annotations

from pathlib import Path

from src.tools.base import ToolResult


def freshness(path: Path) -> str:
    stat = path.stat()
    return f"{int(stat.st_mtime_ns)}:{stat.st_size}"


def read_file(workspace, args, working_memory) -> ToolResult:
    path = workspace.resolve_path(args.path)
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = workspace.relpath(path)
    # 这里是把读过文件的新鲜度写到工作记忆的！注释掉会导致agent在恢复时无法判断文件是否被修改过，以及读后写等下游功能的异常！
    working_memory.note_file_read(rel, freshness(path))
    return ToolResult("success", text)


def write_file(workspace, args) -> ToolResult:
    path = workspace.resolve_path(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.content, encoding="utf-8")
    return ToolResult("success", f"wrote {workspace.relpath(path)}", changed_files=[workspace.relpath(path)])


def apply_text_patch(workspace, args) -> ToolResult:
    path = workspace.resolve_path(args.path)
    text = path.read_text(encoding="utf-8", errors="replace")
    count = text.count(args.old_text)
    if count != 1:
        return ToolResult("error", f"old_text matched {count} times; expected exactly 1", error_type="patch_nonunique")
    path.write_text(text.replace(args.old_text, args.new_text, 1), encoding="utf-8")
    return ToolResult("success", f"patched {workspace.relpath(path)}", changed_files=[workspace.relpath(path)])


def list_files(workspace, args) -> ToolResult:
    root = workspace.resolve_path(args.path)
    iterator = root.rglob("*") if args.recursive else root.iterdir()
    names = []
    for path in iterator:
        if ".jcode" in path.parts:
            continue
        names.append(workspace.relpath(path) + ("/" if path.is_dir() else ""))
        if len(names) >= args.max_entries:
            break
    return ToolResult("success", "\n".join(names))


def search(workspace, args) -> ToolResult:
    root = workspace.resolve_path(args.path)
    matches = []
    for path in root.rglob("*"):
        if ".jcode" in path.parts or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if args.query in line:
                matches.append(f"{workspace.relpath(path)}:{idx}: {line[:300]}")
                if len(matches) >= args.max_results:
                    return ToolResult("success", "\n".join(matches))
    return ToolResult("success", "\n".join(matches) or "no matches")
