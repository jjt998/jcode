# Windows 子进程默认 GBK 解码警告

## 发现日期

2026-09-07

## 问题现象

执行测试或运行任务时，后台线程出现：

```text
UnicodeDecodeError: 'gbk' codec can't decode byte ...
```

## 根因

`Workspace.build()` 调用 `subprocess.run(..., text=True)` 时没有指定编码，Windows 使用系统默认 GBK 解码 Git 的 UTF-8 输出。终止 shell 时的 `taskkill` 调用也存在同样风险。

## 影响

Git 元数据读取可能回退到默认值，测试日志被异常警告污染。该问题不直接改变 `apply_text_patch` 的精确匹配规则。
