# patch_text 跨行 old_text 换行不一致

## 问题

`read_file` 通过文本模式读取时可能将 Windows CRLF 转为 LF，而 `apply_patch` 直接读取原始字节，导致模型依据读取结果生成的跨行 `old_text` 无法命中文件原文。

## 影响

跨行 patch_text 经常返回 `old_text matched 0 times`，反复重试仍然失败。
