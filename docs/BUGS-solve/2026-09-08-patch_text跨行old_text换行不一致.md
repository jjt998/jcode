# patch_text 跨行 old_text 换行不一致

## 解决方案

- `read_file` 和 `apply_patch` 统一使用原始 UTF-8 字节与 `utf-8-sig` 解码，保留 CRLF/LF。
- `read_file` 返回 `newline` 与 `utf8_bom` 元数据。
- `apply_patch` 失败时报告文件换行格式、old_text 是否含换行、BOM 状态，并提示重新读取完整文件。
- schema 明确 `old_text` 必须逐字取自最近一次 `read_file`，不自动做模糊匹配或换行替换。
