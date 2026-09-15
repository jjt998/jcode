# JCode 评测入口

`eval/` 集中保存 JCode 的 Case、fixture、运行器、评分器、回放工具和评测专用测试。

## 基本命令

在仓库根目录使用项目解释器执行：

```powershell
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m eval.cli --project-root . list
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m eval.cli --project-root . run --case checkpoint-resume-r1-valid-checkpoint --keep-workspace
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m eval.cli --project-root . run --all
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m eval.cli --project-root . replay --result-dir eval\reports\<case-id>\<run-id>
```

真实 Provider 配置通过环境变量和本地未提交的 `.jcode.toml` 注入，Case 文件不得保存密钥。

## 代码边界

- `eval/` 只负责评测协议和评测编排，不承载 JCode 产品逻辑。
- `test/` 保留产品单元测试和普通集成测试。
- `eval/tests/` 只验证 Case、运行器、评分器、回放和基线行为。
- `eval/reports/` 只保存本地运行产物，不提交真实运行报告或敏感信息。

## 当前状态

当前已落地 24 个版本化 Case，覆盖 checkpoint/resume、上下文压缩、分层记忆和工具治理；同时提供 workspace 隔离、JCode 子进程运行、证据复制、RunRecord、离线评分、trace 回放和结果汇总。Case 清单以 `eval/manifests/v1.json` 为准，评测协议版本为 `1.0`。
