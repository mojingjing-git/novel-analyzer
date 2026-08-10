# 选书标记 + 队列自动总结 + 总结参数后端持久化

日期：2026-08-02
状态：已批准（用户确认设计各节）

## 目标

1. 聚合/总结页（SummaryPage 共用一个 BookSelector）的下拉中标记已完成最终总结/聚合的书
2. 设置页新增"队列完成后自动总结"开关，队列全部书分析完成后逐本自动执行最终总结
3. 最终总结的并发数/批次参数改为写入后端 config.json（取代 localStorage）

## 1. 选书标记

- 后端 `book_service.book_info` 已返回 `has_report`（`output_dir/final_summary_report*` 存在）与
  `has_aggregated`（`output_dir/aggregated` 存在），无需后端改动
- `frontend/src/api/client.ts`：`BookInfo` 接口补 `has_report?: boolean`、`has_aggregated?: boolean`（可选，兼容旧后端）
- `frontend/src/components/BookSelector.vue`：option 文本 `{{ b.name }} ({{ b.total_chapters }} 章)` 后追加
  `✓已总结`（has_report）/`✓已聚合`（has_aggregated），有哪个标哪个
- 影响：BookSelector 为全局组件，所有页面（时间线/统计/角色卡等）同享标记，无害增值

## 2. 配置（config.json 持久化，仿 auto_archive 模式）

`backend/config/settings.py` `AnalysisConfig` 新增：

- `auto_summary: bool = False` — 队列全部完成后自动总结
- `summary_concurrency: int = 2` — 最终总结并发数（默认 2，避免高并发触发 API 超时）
- `summary_batch_size: int = 30` — 最终总结每批章节数

设置页 `frontend/src/pages/SettingsPage.vue`：auto_archive 同区加：
"队列完成后自动总结"开关（`config.analysis.auto_summary`）、
"总结并发数"输入（`config.analysis.summary_concurrency`，min 1 max 20）、
"总结批次大小"输入（`config.analysis.summary_batch_size`，min 5）

## 3. 队列自动总结（queue_service）

`backend/services/queue_service.py` `_run_queue` 收尾处（`state_change("done", ...)` 前）：

- 仅当**未被用户停止**（非 `_stop_requested`）且 `config.analysis.auto_summary` 为真
- 取队列中 `status == "done"` 的书，**逐本串行**调用 `summary_service.start(book_id, 1, 99999,
  config.analysis.summary_batch_size, config.analysis.summary_concurrency)`，每本等待其完成（轮询 status）后再总结下一本
- 单本失败记录日志继续下一本；自动总结期间用户可通过既有 `/api/summary/stop` 停止
- 进度经既有 WS（summary_progress）广播，前端 QueuePage 已有 hint 显示

## 4. 总结页参数持久化

`frontend/src/pages/SummaryPage.vue`：

- 移除 localStorage 初始化/写入逻辑
- `batchSize`/`concurrency` 初始值改为 onMounted 时 `getSettings()` 读
  `config.analysis.summary_batch_size` / `summary_concurrency`
- 修改后 `putSettings({ analysis: { summary_batch_size, summary_concurrency } })` 写回后端
- 手动总结与自动总结同源配置

## 5. 验证

- 后端：`backend/tests/test_queue_service.py` 相关收尾逻辑单测（mock summary_service）＋
  settings 默认值/读写；全量 pytest 通过
- 前端：vue-tsc exit 0；`npm run build` 重建 dist
- 手动：设置页开关→队列跑完→自动总结触发；总结页改并发→重启后端保留
