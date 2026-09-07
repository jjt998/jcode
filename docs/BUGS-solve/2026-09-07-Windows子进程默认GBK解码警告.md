# Windows 子进程默认 GBK 解码警告

## 解决方案

所有项目内的子进程文本读取显式指定 `encoding="utf-8"` 和 `errors="replace"`：

- Git 元数据读取使用 UTF-8 容错解码。
- Windows `taskkill` 清理输出使用 UTF-8 容错解码。

这样不会再依赖 Windows 系统代码页，也不会因非 ASCII 输出触发后台读取线程的解码警告。
