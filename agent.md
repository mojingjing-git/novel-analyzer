# Agent 工作指南（小说智能分析器）

> **⚠️ 重要提醒：本文件是项目的核心知识库。任何 Agent 在完成修改后，必须同步更新本文件中受影响的章节（模块说明、数据结构、API 端点、配置项等），确保后续 Agent 能快速理解最新代码状态。**

本文件面向在 `小说分析器` 仓库中执行任务的 AI Agent，提供项目背景、架构全景、模块详解、常用命令、硬性约束、当前状态与验证方法。

---

## 1. 项目概述

基于 LLM 的长篇中文网络小说深度分析工具。核心流程：`txt 切分 → 逐章分析 → 滚动总结 → 全书总结`。

- **前端**：Vue 3.5 + TypeScript 5.7 + Vite 6 + Tailwind 4，Win11 Fluent 设计系统
- **后端**：FastAPI（异步），Python 3.11+
- **桌面壳**：pywebview + WebView2（Windows）
- **LLM 兼容**：任何 OpenAI 兼容 API（8+ 厂商预设），以及 Anthropic 协议
- **测试**：101 个测试用例，17 个测试文件

---

## 2. 仓库结构（完整）

```
.
├── backend/                      # FastAPI 后端
│   ├── app.py                    # FastAPI 工厂（CORS、静态文件、路由注册、生命周期）
│   ├── core/                     # 核心分析引擎
│   │   ├── pipeline.py           # 主分析流水线（三阶段）
│   │   ├── llm_client.py         # LLM 客户端（双协议、温退火、三分竞争）
│   │   ├── analyzer.py           # 单章分析引擎
│   │   ├── prompt_builder.py     # Prompt 构建器（9路上下文注入）
│   │   ├── knowledge_base.py     # 知识库管理器（增量合并、缓存、快照隔离）
│   │   ├── memory_state.py       # 内存状态容器（全内存IO、检查点）
│   │   ├── style_analyzer.py     # 风格分析器（22统计+8语义维度）
│   │   └── moderation.py         # 内容审核识别
│   ├── services/                 # 业务服务层
│   │   ├── queue_service.py      # 队列编排 + AnalysisService（单例）
│   │   ├── final_summary.py      # 最终总结执行器（4阶段 + 断点续跑）
│   │   ├── splitter_service.py   # 小说切章服务
│   │   ├── workspace_service.py  # 工作区/归档管理
│   │   ├── book_service.py       # 书名目录映射
│   │   ├── summary_service.py    # 总结任务生命周期
│   │   ├── style_service.py      # 风格分析任务生命周期
│   │   └── viz_service.py        # 可视化数据聚合
│   ├── api/                      # REST 路由 + WebSocket
│   │   ├── routes_analysis.py    # 分析控制 + 队列管理（17端点）
│   │   ├── routes_summary.py     # 总结控制（3端点）
│   │   ├── routes_books.py       # 书籍数据（10端点）
│   │   ├── routes_viz.py         # 可视化数据（3端点）
│   │   ├── routes_workspace.py   # 工作区管理（5端点）
│   │   ├── routes_splitter.py    # 切章（4端点）
│   │   ├── routes_aggregate.py   # 聚合 + Excel 导出（4端点）
│   │   ├── routes_foreshadow.py  # 伏笔分类（1端点）
│   │   ├── routes_prompt.py      # Prompt 预览（1端点）
│   │   ├── routes_settings.py    # 配置管理（6端点）
│   │   └── ws.py                 # WebSocket /ws/progress
│   ├── config/
│   │   ├── settings.py           # 三层 dataclass 配置 + ConfigManager
│   │   └── constants.py          # 所有硬编码默认值 + 50类伏笔定义
│   ├── models/
│   │   ├── analysis_result.py    # AnalysisResult 9类结构化输出
│   │   └── knowledge.py          # KnowledgeBase 12类跨章记忆
│   ├── utils/
│   │   ├── json_utils.py         # 8级 JSON 容错修复链
│   │   ├── foreshadow_ledger.py  # 伏笔账本（状态机）
│   │   ├── aggregate_utils.py    # 聚合工具（11种 JSON 输出）
│   │   ├── character_card_generator.py  # 角色卡片生成器
│   │   ├── character_graph.py    # 角色关系图（pyvis+networkx）
│   │   ├── excel_export.py       # Excel 导出（11个 sheet）
│   │   ├── export_utils.py       # Markdown 导出
│   │   └── text_utils.py         # 编码检测、文本去重、伏笔去重
│   ├── workers/                  # 后台任务
│   └── tests/                    # pytest（101 用例，17 文件）
├── frontend/                     # Vue 3 前端
│   ├── src/
│   │   ├── api/
│   │   │   ├── client.ts         # 53 个 API 方法 + TypeScript DTO
│   │   │   └── useProgressSocket.ts  # 单例 WebSocket + pub/sub
│   │   ├── components/
│   │   │   ├── AppLayout.vue     # 根布局（标题栏 + 侧边栏 + pywebview 窗口控制）
│   │   │   ├── ChapterDetailPanel.vue  # 章节详情面板（自动跟踪最新章节）
│   │   │   ├── BookSelector.vue  # 通用书籍选择器
│   │   │   ├── ChapterValue.vue  # 递归数据渲染组件
│   │   │   ├── LogConsole.vue    # 日志控制台
│   │   │   ├── ProgressBar.vue   # 进度条（含块→章转换）
│   │   │   ├── TokenBadge.vue    # Token 用量徽章
│   │   │   ├── ConfirmDialog.vue # 玻璃材质确认对话框
│   │   │   └── Icon.vue          # 30+ SVG 图标
│   │   ├── composables/
│   │   │   └── useLogStore.ts    # 全局日志存储（localStorage 持久化）
│   │   ├── pages/
│   │   │   ├── QueuePage.vue     # 主页：队列管理 + 实时进度
│   │   │   ├── SettingsPage.vue  # 配置：API/分析/伏笔分类/滚动总结
│   │   │   ├── SummaryPage.vue   # 聚合 + 最终总结（4阶段加权进度）
│   │   │   ├── SplitterPage.vue  # 批量切章
│   │   │   ├── WorkspacePage.vue # 工作区/归档管理
│   │   │   ├── TimelinePage.vue  # 时间线（事件+伏笔，分类筛选）
│   │   │   ├── GraphPage.vue     # 角色关系图（SVG 力导向）
│   │   │   ├── MapPage.vue       # 地图（SVG 树形布局）
│   │   │   ├── CharacterCardPage.vue  # 角色数据库
│   │   │   ├── StylePage.vue     # 风格分析
│   │   │   ├── StatsPage.vue     # Token 统计面板
│   │   │   └── PromptPreviewPage.vue  # Prompt 预览
│   │   ├── utils/
│   │   │   └── markdown.ts       # 零依赖 Markdown→HTML 渲染器
│   │   ├── router.ts             # 12 条路由（AppLayout 包裹）
│   │   ├── App.vue
│   │   ├── main.ts
│   │   └── main.css              # Win11 Fluent 设计系统（--win-* 变量）
│   ├── package.json
│   └── vite.config.ts
├── desktop.py                    # pywebview 桌面入口（单实例锁 + 关闭确认 + uvicorn 后台线程）
├── config.json                   # 运行配置（含 API Key，已 gitignore）
├── queue_state.json              # 队列状态（已 gitignore）
├── workspace/                    # 分析工作区（已 gitignore）
├── docs/superpowers/             # 设计/实施计划
├── agent.md                      # 本文件
├── README.md                     # 项目说明
├── CHANGELOG.md                  # 更新日志
└── requirements.txt              # Python 依赖
```

---

## 3. 技术栈详情

### 后端
| 组件 | 版本/说明 |
|---|---|
| Python | 3.11+ |
| FastAPI | 异步框架，桌面端动态端口（desktop.py `_find_free_port`）；开发模式示例 8088 |
| OpenAI SDK | `AsyncOpenAI` 支持任何兼容 API |
| Anthropic SDK | `AsyncAnthropic` 支持 Claude 系列 |
| pywebview | 桌面 WebView2 壳 |
| openpyxl | Excel 导出（懒加载） |
| networkx + pyvis | 角色关系图（可选依赖） |
| json5 / json_repair | JSON 容错解析 |
| pytest | 101 个测试用例 |

### 前端
| 组件 | 版本/说明 |
|---|---|
| Vue | 3.5（Composition API） |
| TypeScript | 5.7 |
| Vite | 6 |
| Tailwind CSS | 4 |
| Vue Router | 4 |
| 设计系统 | Win11 Fluent（`--win-*` CSS 变量） |

---

## 4. 核心架构

### 4.1 数据流全景

```
.txt 文件
  → splitter_service（切章，13种正则模式）
  → blocks/*.txt
  → pipeline（三阶段分析）
      → serial warmup（前 N 块串行）
      → streaming concurrent（信号量+as_completed 并发）
      → failure retry（最多 3 轮串行补跑）
  → MemoryState（全内存 IO）
  → output/chapter_N_result.json
  → output/rolling_summary.json
  → 最终保存（merge_results → 裁剪 → 写盘）
      ↓
  → final_summary（4阶段总结）
      → batch volumes + reconciliation
      → foreshadow recheck
      → style extraction
      → final report
  → final_summary_report.md
  → aggregate_utils → aggregated/*.json（11种）
  → style_analyzer → style.md
  → character_graph → graph.html
  → excel_export → *.xlsx
```

### 4.2 三阶段分析流水线（pipeline.py）

**阶段一：串行预热**
- 前 `concurrency` 个块串行处理
- 建立初始 KnowledgeBase，保证后续块读不到未来章节知识

**阶段二：流式并发**
- `asyncio.Semaphore` 限流 + `as_completed` 流式调度
- 每 N 块触发一次 rolling summary（后台 asyncio.Task）
- 每 `checkpoint_interval` 批触发检查点落盘
- `state.get_kb_snapshot(chapter_limit)` 实现 KB 快照隔离

**阶段三：失败补跑**
- 最多 3 轮串行重试失败块
- 每轮只重试上一轮标记为失败的块

### 4.3 LLM 客户端（llm_client.py）

**三层重试链：**
1. 温度退火：`temp_max_retries` 次，温度 = max(0, 初始 - attempt × step)
2. 指数退避：`backoff_max_retries` 次，等待 = min(2^N, 60s)
3. 429 限流：额外 3 轮，尊重 Retry-After 头

**三分竞争（单次 chat 调用）：**
- API 任务 / 停止事件等待 / 硬超时（timeout + 30s）
- `asyncio.wait(FIRST_COMPLETED)` 任一先完成即返回

**双协议支持：**
- `detect_provider()`：按 base_url/api_key/model 自动检测 OpenAI 或 Anthropic
- Anthropic：提取 system 消息、合并相邻同角色消息、拼接 text block
- OpenAI：`extra_body` 注入思考控制、`response_format` json_object 模式

**思考链清洗：** `_strip_thinking()` 移除 `<think>...</think>`、`<reasoning>...</reasoning>` 等标签

### 4.4 Prompt 构建（prompt_builder.py）

**KV Cache 布局（变化频率低→高）：**
```
system: SYSTEM_PROMPT + 50类伏笔表 + 已知世界观 + 主题元素  ← 恒定前缀
user:   时间线 + 角色状态 + 角色关系 + 已验证事实 + 前情摘要 + 伏笔网络 + 故事历史 + 章节正文
```

**九路上下文注入：**
1. 完整故事历史（结构化滚动总结 + 近期逐章 timeline）
2. 前情摘要（最近 N 章）
3. 当前时间线
4. 角色当前状态
5. 角色关系
6. 已验证事实
7. 已知世界观
8. 主题元素
9. 伏笔网络

### 4.5 知识库管理（knowledge_base.py）

**三种 KB 构建方式：**
1. `merge_results(output_dir)` — 全量合并（最终保存用，带裁剪）
2. `build_temp_knowledge(output_dir, chapter_limit)` — 带缓存的临时 KB
3. `_merge_incremental(kb, new_results)` — 增量更新（缓存命中时）

**内存缓存（`_temp_kb_cache`）：**
- key: `(output_dir, chapter_limit)`
- value: `(fingerprint, results, kb)`
- 支持近邻增量：找缓存中 limit 最大且 ≤ 当前值的条目
- 精确匹配/指纹比对/mtime 检测变化文件

### 4.6 内存状态（memory_state.py）

**核心数据：**
```python
self.results: Dict[int, AnalysisResult]    # {章号: 结果}
self.kb: KnowledgeBase                     # 增量维护的 KB
self.rolling: dict                         # 滚动总结数据
self._flushed_chapters: Set[int]           # 已落盘的章号集合
```

**KB 快照隔离（`get_kb_snapshot(chapter_limit)`）：**
- 三级缓存策略：精确命中 / 增量扩展 / 从子集重建
- **P0-1 修复**：只允许请求 limit ≥ 缓存 limit 才扩展，防止并发低章号块读到未来知识

**乱序保护（`_merge_one`）：**
- `_kb_last_chapter` 跟踪最大已合并章号
- 低章号迟到结果只合并非进度内容（事实/角色/世界等），跳过 timeline/recent_summaries

### 4.7 最终总结（final_summary.py）

**4 阶段：**
1. **分卷摘要 + 伏笔调和**（每批两次 LLM 调用）：
   - 第一次：Markdown 卷摘要
   - 第二次：JSON reconciliation（伏笔状态调和）
   - 卷摘要一完成即落盘（断点续跑）
2. **全书伏笔复检**（剩余 active 伏笔，40个/子批）
   - **复检超限保护**：全量卷摘要超 `RECHECK_FULLTEXT_BUDGET_CHARS`（默认 15 万字符）时按伏笔埋设章砍埋设前卷（召回无损），仍超则跳过该组不调用 LLM
3. **风格分析**：`extract_style_profile` → 注入最终报告
4. **最终报告**：卷摘要 + 伏笔上下文 + 审计 + 风格 → `final_summary_report.md`

**伏笔总表构建：**
```
收集 all_clues → type 归一化(50类直接用 / per-book映射 / fallback) 
  → 三维过滤(importance/confidence/category) → 语义去重 
  → 综合排序(imp×100 + conf×10 + evidence_count) → 分层截断(高500/中200)
```

**断点续跑：** `final_summary_checkpoint/` 目录，`volume_{idx}.md` + `recon_{idx}.json` + `manifest.json`

### 4.8 滚动总结（pipeline.py 中的 _async_rolling）

**五层截断策略：**
1. `paradigm_layers`（≤5）— 范式层
2. `milestones` — FIFO 淘汰 + 锁定首条 + 范式层首条
3. `momentum` — 归档触发后清空
4. `causal_chains`（≤5）— 因果链
5. `milestone` 单条（≤50字）

**势头归档：** 跨阈值/跨窗口 → LLM 压缩为一条里程碑

---

## 5. 后端模块详解

### 5.1 核心模块（backend/core/）

#### pipeline.py（1064+行）
- **职责**：全书分析总调度器
- **关键类**：`AnalysisPipeline`
- **关键方法**：`run()`、`_serial_warmup()`、`_streaming_concurrent()`、`_failure_retry()`、`_async_rolling()`、`_flush_checkpoint()`
- **进度广播**：通过 `ProgressHub` WebSocket 推送 log/progress/block_done/state_change/token_stats
- **停止机制**：`self._stop_requested` + `self.analyzer.stop()` + `self.rolling_client.request_stop()`

#### llm_client.py（1003行）
- **职责**：统一封装 OpenAI/Anthropic 双协议 LLM 调用
- **关键类**：`LLMClient`
- **关键方法**：`chat()`、`chat_with_retry()`、`list_models()`、`probe_thinking_params()`、`detect_provider()`
- **KV Cache 统计**：读取 `prompt_tokens_details.cached_tokens`
- **FailureLogger**：线程安全，24h 滚动日志，记录温度/错误类型/等待时长

#### analyzer.py（209行）
- **职责**：单章分析引擎
- **关键类**：`ChapterAnalyzer`
- **流程**：`prompt_builder.build_messages()` → `llm_client.chat_with_retry(validate=validate_json_response)` → `_parse_response()` → `AnalysisResult.from_dict()`
- **JSON 验证**：检查必须字段（core_events/cross_block/long_context_insights）

#### prompt_builder.py（307行）
- **职责**：构建 system + user 两条消息
- **关键类**：`PromptBuilder`
- **50类伏笔表**：`FORESHADOW_CATEGORY_TEXT` 恒定追加在 system prompt 后
- **结构化滚动总结渲染**：`_render_structured_rolling()` — 五层渲染

#### knowledge_base.py（464行）
- **职责**：磁盘 KB 的加载/保存/合并/增量更新
- **关键类**：`KnowledgeBaseManager`
- **关键方法**：`merge_results()`、`build_temp_knowledge()`、`_merge_incremental()`、`_merge_result_into()`

#### memory_state.py（398行）
- **职责**：全内存 IO，持有所有 AnalysisResult + 增量 KB
- **关键类**：`MemoryState`
- **关键方法**：`merge_one()`、`get_kb_snapshot()`、`flush_to_disk()`、`restore_from_disk()`

#### style_analyzer.py（494行）
- **两层架构**：
  1. 22 项统计硬指标（纯代码）：句法、对话、词汇、词表密度、标点
  2. 8 维语义风格（LLM）：signature_expressions/narrative_rhythm/dialogue_style/rhetorical_preferences/emotional_expression/narrative_voice/information_control/narrator_and_genre
- **采样策略**：前半随机 3 章 + 后半随机 3 章，每章随机位置取 1500 字

#### moderation.py（64行）
- **三层检测**：结构化错误码 + 自由文本关键词 + 隐式信号
- **标记机制**：`[MODERATION]` 前缀注入错误字符串

### 5.2 服务层（backend/services/）

#### queue_service.py（741行）
- **QueueManager**：队列状态机（pending/running/done/failed/skipped）
- **AnalysisService**（单例）：
  - `_run_queue()`：逐本运行 `AnalysisPipeline`
  - 双向互斥：分析运行中禁总结，总结运行中禁分析
  - 自动总结：队列完成后逐本串行执行 `FinalSummaryRunner`
  - 自动归档：分析完成后 `shutil.move` 到 `分析结果/`
  - 每本书分析完成后 token 消耗落盘 `output/token_stats.json`
- **状态持久化**：`queue_state.json`（相对路径存储）

#### final_summary.py（1140+行）
- **职责**：全书总结执行引擎
- **关键类**：`FinalSummaryRunner`
- **关键方法**：`run()`、`_run_batch()`、`_reconcile_batch()`、`_recheck_remaining()`、`_write_report()`、`_plan_recheck_batches()`

#### summary_service.py
- 总结任务生命周期管理
- 总结结束落盘 `output/summary_token_stats.json`

#### splitter_service.py（707行）
- **职责**：小说文本切分为独立章节文件
- **13 种章节正则**：中文数字、阿拉伯数字、"回"、"节"、卷+章组合、英文 Chapter 等
- **核心算法**：两遍扫描（定位边界→提取内容）、评分式模式选择、MD5 去重、超长章拆分
- **并发写入**：`ThreadPoolExecutor`（最多 32 工作者）缓解网络盘延迟

#### workspace_service.py（365行）
- **职责**：工作区目录管理
- **安全机制**：`_is_within()` + `_safe_join()` 路径遍历防护
- **平台回收站**：Windows `SHFileOperationW` + macOS `osascript` + Linux `gio trash`

#### book_service.py（197行）
- **职责**：书名→目录映射（catalog）
- **发现来源**：队列项 + 文件系统扫描 + 归档目录 + 手动注册
- **懒刷新**：`get_book_path()` 在找不到 ID 时触发 `refresh_books()`

#### viz_service.py（168行）
- **三种可视化数据**：
  1. `timeline_data()` — 事件 + 伏笔（含分类映射）
  2. `graph_data()` — 角色节点 + 共现边
  3. `map_data()` — 地点 + 空间关系

### 5.3 工具层（backend/utils/）

#### json_utils.py（297行）
- **8级修复链**：直接解析 → 截尾 → 去注释 → 漏引号修复(两轮) → json5 → ast.literal_eval → json_repair → 单引号替换
- **原子写**：`safe_save_json()` 先写 `.tmp` 再 `replace`

#### foreshadow_ledger.py（189行）
- **状态机**：`active → resolved / dormant`
- **休眠判定**：`last_seen_chapter` 距当前进度 ≥ 200 章

#### aggregate_utils.py（882行）
- **职责**：逐章 JSON 结果聚合为 11 种输出格式
- **关键类**：`JSONAggregator`
- **去重机制**：角色名归一化（剥离角色后缀）、哈希去重、模糊主题去重

#### character_card_generator.py（721行）
- **职责**：生成角色详细档案卡片
- **输出格式**：text / HTML / Markdown
- **安全**：HTML 输出通过 `_esc()` 转义防 XSS

#### character_graph.py（360行）
- **职责**：生成 Dyson Sphere 风格角色关系图（独立 HTML）
- **可选依赖**：networkx（图算法）+ pyvis（HTML 生成）
- **算法**：连通分量检测 → 颜色映射 → spring layout → 自定义 HTML patching

#### text_utils.py（359行）
- **编码检测**：BOM 识别 + UTF-16 NUL 密度检测（I-7 修复）
- **文本去重**：归一化相等 + 子串包含 + SequenceMatcher 相似度
- **伏笔去重**：关键词倒排索引 + 序列相似度（PERF-2 优化）

### 5.4 API 层（backend/api/）

**总计约 57 个 HTTP 端点 + 1 个 WebSocket 端点**

| 路由模块 | 端点数 | 前缀 | 说明 |
|---|---|---|---|
| `routes_analysis.py` | 18 | `/api` | 分析 start/stop/status + token_stats（会话/累计）+ 队列 CRUD + 风格 + 日志 |
| `routes_books.py` | 10 | `/api/books` | 书籍列表/结果/报告/账本/角色/章节/token_stats |
| `routes_settings.py` | 6 | `/api/settings` | 配置读写/模型列表/思维探测/预设 |
| `routes_workspace.py` | 5 | `/api/workspace` | 工作区小说/归档管理 |
| `routes_splitter.py` | 4 | `/api/splitter` | 预览/保存/批量切章 |
| `routes_aggregate.py` | 4 | `/api/aggregate` | 聚合/文件列表/读取/Excel |
| `routes_summary.py` | 3 | `/api/summary` | 总结 start/stop/status |
| `routes_viz.py` | 3 | `/api/viz` | 时间线/关系图/地图 |
| `routes_prompt.py` | 1 | `/api/prompt` | Prompt 预览 |
| `routes_foreshadow.py` | 1 | `/api/foreshadow` | 50类伏笔分类定义 |
| `ws.py` | 1 WS | `/ws/progress` | WebSocket 实时进度 |

**安全机制：**
- 路径遍历防护（`routes_aggregate.py`）
- WebSocket 来源白名单（仅 localhost/127.0.0.1）
- API Key 在 POST body 中（避免 URL/日志泄露）
- 分析锁（`_ensure_idle()` / `_require_analysis_idle()`）

---

## 6. 前端模块详解

### 6.1 API 层

#### client.ts（366行）
- **53 个 API 方法**，覆盖所有后端端点
- **TypeScript DTO**：`AppConfigDto`、`QueueItemDto`、`AnalysisStatus`、`TokenStatsResponse` 等
- **错误增强**：HTTP 错误附加 `.status` 和 `.detail` 属性
- **防重复读取**：先 `res.text()` 再 `JSON.parse()`，避免 body stream already read

#### useProgressSocket.ts（123行）
- **单例 WebSocket**：模块级变量，跨路由共享
- **pub/sub 模式**：组件注册 `onMessage` 回调，自动清理
- **指数退避重连**：1s → 2s → 4s → ... → 10s 上限
- **ping 过滤**：心跳消息静默丢弃
- **手动关闭保护**：`manualClose` 标志防止孤立重连

### 6.2 状态管理

**无 Vuex/Pinia**，采用：
- **模块级单例**：`useLogStore`（2000 条容量，localStorage 持久化，500ms 节流保存）+ `useProgressSocket`
- **组件内 ref()**：每个页面管理自己的状态
- **后端为真相源**：配置在后端持久化，前端挂载时读取

### 6.3 设计系统（main.css）

**Win11 Fluent：**
- CSS 变量：`--win-accent`、`--win-mica`、`--win-layer`、`--win-stroke`、`--win-shadow-*`、`--win-radius-*`
- 玻璃材质容器：`glass-card`、`glass-button`、`glass-input`、`glass-badge`
- 禁止 `backdrop-filter`（WebView2 不支持）

### 6.4 页面（12个）

| 页面 | 行数 | 职责 |
|---|---|---|
| QueuePage | 453 | 主页：队列表、启停控制、实时进度（WS+轮询）、章节详情、日志 |
| SettingsPage | 489 | 配置：API 预设、模型选择器、思维探测、伏笔分类网格、滚动总结参数、复检批大小 |
| SummaryPage | 498 | 聚合+总结：4阶段加权进度、模型覆盖、日志抽屉、报告查看器 |
| SplitterPage | 419 | 批量切章：文件选择、预览、自定义正则、卷识别、pywebview 文件对话框 |
| WorkspacePage | 185 | 工作区/归档：列表、归档、删除（自定义确认对话框） |
| TimelinePage | 249 | 时间线：事件+伏笔、重要性/分类筛选 |
| GraphPage | 191 | 角色关系图：SVG 静态环形布局、节点点击高亮 |
| MapPage | 225 | 地图：SVG 树形布局、空间关系虚线 |
| CharacterCardPage | 259 | 角色数据库：统计、弧光、事件、状态演化、关系 |
| StylePage | 139 | 风格分析：启停、轮询、结果展示 |
| StatsPage | 181 | Token 统计：分类明细、每章详情、按书历史统计、5s 自动刷新 |
| PromptPreviewPage | 55 | Prompt 预览：系统+用户消息、可指定章节号、字符计数 |

### 6.5 组件（8个）

| 组件 | 行数 | 职责 |
|---|---|---|
| AppLayout | 348 | 根布局：标题栏、可折叠侧边栏、pywebview 窗口控制、暗色模式 |
| ChapterDetailPanel | 244 | 章节详情：自动跟踪最新章节（60s 空闲回退）、请求序列守卫 |
| BookSelector | 69 | 书籍下拉选择器：状态指示器、刷新按钮 |
| ChapterValue | 130 | 递归数据渲染：标量/数组/对象、中文字段标签 |
| LogConsole | 110 | 日志控制台：简化/完整模式、自动滚动 |
| ProgressBar | 41 | 进度条：块→章转换、ETA 显示 |
| TokenBadge | 85 | Token 徽章：分类明细、紧凑/完整模式 |
| ConfirmDialog | 82 | 确认对话框：玻璃材质、危险样式 |
| Icon | 98 | SVG 图标库：30+ 图标 |

### 6.6 前端关键模式

- **双更新策略**：WebSocket 实时推送 + 5s REST 轮询降级
- **防抖刷新**：高频 WS 事件（block_done/token_stats）500ms 防抖
- **请求序列守卫**：单调递增计数器防止旧异步响应覆盖新数据
- **空闲自动回退**：ChapterDetailPanel 60s 无操作自动回到最新章节
- **参数持久化**：SummaryPage 使用版本计数器+防抖防止并发 PUT 竞态

---

## 7. 数据模型

### 7.1 AnalysisResult（analysis_result.py，286行）

9 类结构化输出：
```python
core_events          # 核心事件（id, event, characters, function）
character_arcs       # 角色弧光（surface_action, deep_motivation, change_delta, driver）
foreshadowing        # 伏笔（clue, type, confidence, importance）
plot_holes           # 逻辑漏洞
locations            # 地点（name, parent, type, description）
spatial_relationships  # 空间关系（from, to, relation）
cross_block          # 跨章衔接（summary, unresolved_questions, new_leads）
updated_knowledge    # 知识库更新
long_context_insights  # 长上下文洞察（thematic_elements, character_relationships, world_building）
```

### 7.2 KnowledgeBase（knowledge.py，116行）

12 类跨章记忆：
```python
story_timeline           # 故事时间线
recent_summaries         # 近期逐章摘要
compressed_arcs          # 压缩弧光
character_states         # 角色当前状态
verified_facts           # 已验证事实
long_term_arcs           # 长期弧光
character_relationships  # 角色关系
world_building           # 世界观
thematic_elements        # 主题元素
foreshadowing_network    # 伏笔网络
rolling_summary          # 滚动总结（原始文本）
rolling_structured       # 结构化滚动总结（paradigm_layers/milestones/momentum/causal_chains）
```

### 7.3 配置结构（settings.py）

```python
AppConfig:
  api: APIConfig          # base_url, model, api_key, provider, max_tokens, timeouts, retries, thinking, json_mode
  analysis: AnalysisConfig  # concurrency, block_size, arc lengths, foreshadow params, rolling summary params, foreshadow_recheck_batch_size（默认 40）
  gui: GUIConfig          # theme, font_size, window_width/height
  working_directory: str
  knowledge_file: str

APIConfig 预设：8+ 厂商（OpenAI/Anthropic/DeepSeek/Qwen/GLM/MiniMax/Moonshot/...）
```

---

## 8. 常用命令

### 后端

```bash
# 安装依赖
pip install -r requirements.txt

# 启动后端（开发）
python -m uvicorn backend.app:app --reload --port 8088

# 运行测试
python -m pytest

# 运行指定测试
python -m pytest backend/tests/test_pipeline.py -v
```

### 前端

```bash
cd frontend

# 安装依赖
npm install

# 开发模式
npm run dev

# 类型检查 + 生产构建
npm run build
```

### 桌面运行

- Windows 下直接运行 `run_desktop.bat`
- 前端构建产物由 `build_frontend.bat` 生成到 `frontend/dist`
- `desktop.py`：pywebview 启动 → uvicorn 后台线程 → 端口发现 → UTF-8 编码修复

---

## 9. 硬性约束（必须遵守）

### 9.1 密钥与数据安全
- `config.json` 含真实 API Key，**禁止提交、打印、写入日志或纳入任何输出**
- `config.json`、`queue_state.json`、`workspace/`、`*.log`、`app.lock`、`1/`、`.bak_*` 均已被 `.gitignore` 忽略

### 9.2 行尾符
- 仓库中存在大量历史 CRLF/LF 差异，**不要批量转换**
- 查看真实改动：`git diff -w -- <file>`

### 9.3 WebView2 兼容
- **禁止** `backdrop-filter` / `-webkit-backdrop-filter`
- 动画使用 DOM 元素 + `transform` / `opacity`
- Win11 Mica 风格使用纯色/低透明层

### 9.4 前端设计系统
- 所有颜色/圆角/阴影/字体/间距必须引用 `--win-*` 变量或 Tailwind 工具类
- 禁止硬编码 iOS 色值
- Win11 强调色克制使用

### 9.5 不要破坏业务
- 前端改动只允许调整视觉层，不得改变 props、事件、路由、API 调用、数据结构
- 后端改动除非任务明确要求，否则默认不触碰

### 9.6 ⚠️ 修改后必须更新 agent.md
- **任何代码变更后**，检查本文件中受影响的章节并同步更新
- 更新范围：模块说明、行数、数据结构、API 端点、配置项、已知问题等
- 这是确保后续 Agent 能快速理解项目状态的关键

---

## 10. 当前状态

### 10.1 Win11 前端重做已完成主体
- `main.css` 已重构为 Win11 Fluent 设计系统
- `AppLayout.vue` 已实现 Win11 风格：48px 标题栏、可折叠侧边栏
- 所有页面/组件已切换到 `--win-*` 变量
- 旧的 `sweep.ts`、`ensure-backdrop.mjs` 已删除

### 10.2 已知待修复项

`ConfirmDialog.vue` 仍有 Win11 规范偏差：
```css
.confirm-card { border-radius: 20px; }   /* 应改为 8px */
.confirm-title { font-size: 17px; }       /* Win11 建议 20px Semibold */
.confirm-mask { background: rgba(0,0,0,0.3); }  /* 建议 0.4 */
```

### 10.3 2026-08-17 变更摘要
- **复检上下文超限保护**：`RECHECK_FULLTEXT_BUDGET_CHARS=150000`、`_plan_recheck_batches()` 按伏笔埋设章砍卷
- **复检批大小配置化**：`AnalysisConfig.foreshadow_recheck_batch_size`（默认 40），前端 SettingsPage 可调
- **Token 统计可见性**：每本书分析/总结结束落盘 token_stats.json，新增 `GET /api/books/{book_id}/token_stats`；前端 StatsPage 按书历史卡片、SummaryPage 本次 token 行
- **桌面端安全**：`desktop.py` 单实例锁（app.lock）+ 关闭确认（events.closing veto + Api.close 确认）
- **Prompt 预览增强**：支持指定章节号输入
- **工程卫生**：`.gitignore` 补 `app.lock`/`1/`/`.bak_*`；`run_desktop.bat` 去硬编码路径改用 py 启动器；`build_frontend.bat` 排除备份目录；回收站清理 13 项备份残留

### 10.4 2026-08-18 变更摘要
- **本次窗口会话 token 累计**：分析侧 `AnalysisService.token_stats()`（单例跨书累计，天然会话级）+ 总结侧 `SummaryService.session_token_stats()`（`_session_token_stats` 内存累计器，每次总结任务 finally 累加分类小计与耗时），新增 `GET /api/analysis/token_stats/session`（分析+总结两段）；前端 QueuePage 顶部卡片展示 5 项：输入 / 输出 / 命中缓存 / 命中率（仅分析侧口径：cached ÷ (input+cached)）/ 总消耗（input+cached+output），内存态重启清零
- **侧边栏宽度**：`AppLayout.vue` `.sidebar` 展开宽度 280px → 186.67px（2/3）
- 新增测试 `test_session_token_stats.py`（3 用例：跨任务累计/空结构/端点结构）

### 10.3 相关文档
- Win11 重做计划：`docs/superpowers/plans/2026-08-16-win11-frontend-redesign.md`
- 项目 README：`README.md`
- 更新日志：`CHANGELOG.md`

---

## 11. 验证清单

### 前端改动
```bash
cd frontend && npm run build

# 不应有 backdrop-filter
grep -RIn "backdrop-filter" frontend/src || true

# 不应有旧 iOS 硬编码色
grep -RIn "#007AFF\|#0A84FF\|#5AC8FA\|#FF3B30" frontend/src || true

# 不应有 sweep/shimmer 残留
grep -RIn "sweep\|shimmer" frontend/src || true
```

### 后端改动
```bash
# 运行测试
python -m pytest

# 类型检查（如有 mypy）
mypy backend/
```

---

## 12. Agent 行为准则

1. **先读后改**：修改前先查看目标文件和相关上下文
2. **小步提交**：按文件/组件/页面拆分，避免一次性大改
3. **保持行尾**：不要批量格式化
4. **构建验证**：前端改完必须 `npm run build` 通过
5. **测试验证**：后端改完必须 `pytest` 通过
6. **不启动常驻服务**：除非任务明确要求
7. **遇到模糊需求先确认**：不要擅自扩大范围
8. **不修改用户明确要求"不要修改"的内容**
9. **⚠️ 修改后更新 agent.md**：完成变更后，检查并更新本文件中受影响的章节，确保知识库与代码同步
