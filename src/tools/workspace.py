from __future__ import annotations

from pathlib import Path

from src.tools.base import ToolResult


def freshness(path: Path) -> str:
    stat = path.stat()
    return f"{int(stat.st_mtime_ns)}:{stat.st_size}"


def read_file(workspace, args, working_memory) -> ToolResult:
    path = workspace.resolve_path(args.path)
    source_text = path.read_text(encoding="utf-8", errors="replace")
    # 先按 start/end 截取，再交给 max_chars 控制返回长度，避免读取范围和结果长度混在一起。
    selected_text = source_text[args.start : args.end]
    missing_chars = max(0, len(selected_text) - args.max_chars)
    returned_text = selected_text[: args.max_chars]
    rel = workspace.relpath(path)
    current_freshness = freshness(path)
    complete = missing_chars == 0
    file_size = path.stat().st_size
    read_metadata = {
        "complete": complete,
        "returned_chars": len(returned_text),
        "missing_chars": missing_chars,
        "file_size": file_size,
        "freshness": current_freshness,
    }
    # 这里是把读过文件的新鲜度写到工作记忆的！注释掉会导致agent在恢复时无法判断文件是否被修改过，以及读后写等下游功能的异常！
    working_memory.note_file_read(rel, args.model_dump(), current_freshness, read_metadata)
    # 工具历史和 artifact 需要知道这段内容对应的文件版本，文件变更后才能标记为过期。
    # 协议头直接进入模型上下文，避免模型从正文形状猜测是否已读到范围末尾。
    header = (
        "[read_file]\n"
        f"complete: {str(complete).lower()}\n"
        f"returned_chars: {len(returned_text)}\n"
        f"missing_chars: {missing_chars}\n"
        f"file_size: {file_size} bytes\n"
        f"freshness: {current_freshness}\n\n"
    )
    return ToolResult(
        "success",
        header + returned_text,
        metadata={"source_files": [{"path": rel, "freshness": current_freshness}], **read_metadata},
    )


def write_file(workspace, args) -> ToolResult:
    path = workspace.resolve_path(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.content, encoding="utf-8")
    return ToolResult("success", f"wrote {workspace.relpath(path)}", changed_files=[workspace.relpath(path)])


def apply_text_patch(workspace, args) -> ToolResult:
    path = workspace.resolve_path(args.path)
    raw = path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig", errors="replace")
    count = text.count(args.old_text)
    if count != 1:
        newline = "crlf" if "\r\n" in text else "lf"
        return ToolResult("error", f"old_text matched {count} times; expected exactly 1", error_type="patch_nonunique", metadata={"path": workspace.relpath(path), "match_count": count, "freshness": freshness(path), "newline": newline, "utf8_bom": has_bom})
    path.write_bytes((b"\xef\xbb\xbf" if has_bom else b"") + text.replace(args.old_text, args.new_text, 1).encode("utf-8"))
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
    source_files = []
    for path in root.rglob("*"):
        if ".jcode" in path.parts or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # 搜索结果也依赖被扫描文件，记录 freshness 以便文件变更后让旧搜索证据失效。
        source_files.append({"path": workspace.relpath(path), "freshness": freshness(path)})
        for idx, line in enumerate(text.splitlines(), start=1):
            if args.query in line:
                matches.append(f"{workspace.relpath(path)}:{idx}: {line[:300]}")
                if len(matches) >= args.max_results:
                    return ToolResult("success", "\n".join(matches), metadata={"source_files": source_files})
    return ToolResult("success", "\n".join(matches) or "no matches", metadata={"source_files": source_files})
