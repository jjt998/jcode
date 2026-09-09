# Harness 组件耗时日志设计

## 目标

记录 Harness 各内部组件的墙钟耗时，并在组件完成时同时写入 run trace、最终 report 和控制台。该设计不区分 Agent 与 Harness，不定义 Agent 耗时字段。

## 计时模型

每个组件只写一条 `harness_component_finished` 事件，事件包含 `component`、`operation`、`span_id`、`parent_span_id`、`started_at`、`finished_at`、`duration_ms`、`status` 和 `metadata`。

耗时使用 `time.perf_counter_ns()` 计算，ISO 时间戳只用于展示和审计。组件名称覆盖 `context_build`、`provider_request`、`tool_call`、`checkpoint`、`final_gate` 和 `memory_maintenance`。Run report 额外保存总耗时和按组件聚合的摘要。

## 输出

每条组件完成事件打印一行脱离业务内容的控制台日志：

```text
[harness-timing] run=<run_id> component=<component> operation=<operation> duration=<ms>ms status=<status>
```

控制台不输出模型响应、工具参数或工具结果。完整结构化事件写入 `trace.jsonl`，聚合结果写入 `report.json`。

## 嵌套与汇总

`span_id` 用于标识单次组件调用，`parent_span_id` 用于表达嵌套关系。父子 span 都保留，但聚合展示时不能将父 span 和子 span 重复相加。Provider 重试按单次请求调用记录 `retry_index`。

## 失败语义

组件异常时仍写完成事件，`status` 为 `error`，随后继续沿用现有异常传播和 run 收口流程。不添加重试、降级、默认值或静默吞错行为。

## 验收

正常、异常和中止运行均能在 trace/report 中看到计时；控制台能看到 `[harness-timing]`；组件汇总不包含 Agent/Harness 二分字段；现有测试与计时单测全部通过。
