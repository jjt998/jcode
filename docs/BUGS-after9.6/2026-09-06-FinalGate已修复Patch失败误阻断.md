# FinalGate 已修复 Patch 失败误阻断

## 发现日期

2026-09-06

## 问题现象

一次任务中，`apply_patch` 因中文文本、换行符或 BOM 差异导致 `old_text` 未命中，返回 `patch_nonunique`。随后 Agent 使用同路径 `write_file` 成功完成整文件写入，但最终收口阶段持续出现：

```text
error: final answer must mention unresolved tool failures before finishing
```

任务实际已完成，却可能因为 Final Gate 重复拒绝而持续消耗步骤，最终以 `step_limit_reached` 停止。

## 根因

旧 `TaskState.record_tool()` 将每个非成功工具结果追加到 `failed_tools`，该列表只增不减。旧 `FinalGate` 直接把 `failed_tools` 非空解释为“存在未解决失败”，并用最终答案是否包含 error/失败等关键词决定放行。

因此系统无法表达以下事实：

```text
apply_patch(src/a.py) 失败
  -> write_file(src/a.py) 成功
  -> 原 patch 失败已经由明确操作解决
```

## 影响范围

- 任何可恢复工具失败都会污染本次 run 的最终收口状态。
- 最终答案可被任意失败关键词绕过，也可能在实际完成后被错误阻断。
- Gate 拒绝只写入自然语言提示，缺少失败 ID、路径和修复关联，模型容易重复输出 final。
- `write_file` 仅依赖“曾读取过文件”，外部变更后存在整文件覆盖风险。

## 复现步骤

1. 成功读取 `src/a.py`。
2. 调用 `apply_patch`，传入不完全匹配的中文 `old_text`。
3. 得到 `patch_nonunique`。
4. 调用同路径 `write_file`，写入正确完整文件。
5. 模型返回正常最终答案。
6. Final Gate 仍按历史失败拒绝最终答案。

## 关联解决方案

[2026-09-06-FinalGate已修复Patch失败误阻断.md](../BUGS-solve/2026-09-06-FinalGate已修复Patch失败误阻断.md)

