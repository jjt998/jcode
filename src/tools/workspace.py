from __future__ import annotations

from pathlib import Path

from src.tools.base import ToolResult


def freshness(path: Path) -> str:
    stat = path.stat()
    return f"{int(stat.st_mtime_ns)}:{stat.st_size}"


def _newline_style(text: str) -> str:
    """识别文本的原始换行格式，供读取和补丁诊断共同使用。"""
    has_crlf = "\r\n" in text
    without_crlf = text.replace("\r\n", "")
    has_lf = "\n" in without_crlf
    if has_crlf and has_lf:
        return "mixed"
    if has_crlf:
        return "crlf"
    if has_lf:
        return "lf"
    return "none"


def _normalize_newlines(text: str, style: str) -> str:
    """只规范换行表示，不改变其它字符。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "\r\n") if style == "crlf" else normalized


def read_file(workspace, args, working_memory) -> ToolResult:
    path = workspace.resolve_path(args.path)
    raw = path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    source_text = raw.decode("utf-8-sig", errors="replace")
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
        "newline": _newline_style(source_text),
        "utf8_bom": has_bom,
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
        f"newline: {read_metadata['newline']}\n"
        f"utf8_bom: {str(has_bom).lower()}\n\n"
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
    newline = _newline_style(text)
    match_text = args.old_text
    match_mode = "exact"
    count = text.count(match_text)
    # 模型常把 CRLF 规范化成 LF；仅在纯换行文件且原样未命中时允许等价匹配。
    if count == 0 and newline in {"crlf", "lf"} and ("\n" in match_text or "\r" in match_text):
        match_text = _normalize_newlines(match_text, newline)
        count = text.count(match_text)
        match_mode = "newline_normalized"
    if count != 1:
        has_line_break = "\n" in args.old_text or "\r" in args.old_text
        message = (
            f"old_text matched {count} times; expected exactly 1; "
            f"file_newline={newline}; old_text_contains_newline={str(has_line_break).lower()}; "
            f"utf8_bom={str(has_bom).lower()}; reread the complete file before retrying"
        )
        return ToolResult("error", message, error_type="patch_nonunique", metadata={"path": workspace.relpath(path), "match_count": count, "freshness": freshness(path), "newline": newline, "utf8_bom": has_bom, "old_text_contains_newline": has_line_break, "match_mode": match_mode})
    replacement = _normalize_newlines(args.new_text, newline) if newline in {"crlf", "lf"} else args.new_text
    patched_text = text.replace(match_text, replacement, 1)
    path.write_bytes((b"\xef\xbb\xbf" if has_bom else b"") + patched_text.encode("utf-8"))
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
