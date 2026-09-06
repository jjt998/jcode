# JCode 上下文治理 9.5 实施 Todo

- [x] 阶段 0：读取冻结文档并记录 59 个基线测试
- [x] 阶段 1：模型容量、配置优先级与统一 Provider 输入
- [x] 阶段 2：Continuation、固定占用与容量错误族
- [x] 阶段 3：Working Memory v2、session 5、checkpoint 2
- [x] 阶段 4：压力等级、History 窗口与 list_files 规则
- [x] 阶段 5：Runtime 统一异常边界、审计和系统停止
- [x] 阶段 6：候选构建、Skills 选择与动态 token 预算
- [x] 阶段 7：Level 4 摘要 schema、artifact 与候选事务
- [x] 阶段 8：运行时、checkpoint、Web、全量测试与收口扫描完成

## 收口验收

- 全量测试：`64 passed`
- Python 字节码检查：`compileall ok`
- CLI 入口检查：`python -m src --help` 正常
- `git diff --check`：通过
- 当前工作区未创建 Git commit；改动保留给后续人工审阅。
