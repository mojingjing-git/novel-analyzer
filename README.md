# 小说智能分析器 (Novel Analyzer)

基于 LLM 的长篇中文网络小说深度分析工具。支持从 txt 原始文本一键完成**章节切分 → 逐章智能分析 → 全书最终总结**的全流程，产出主线脉络报告、伏笔台账、角色卡、时间线、关系图、地图、写作风格分析、Excel 导出等十余种分析产物。

技术栈：**FastAPI + Vue 3 + pywebview**（Web 架构，桌面壳包装），LLM 侧兼容任意 OpenAI 兼容 API（MiniMax / DeepSeek / 智谱 / 火山 / 硅基流动 / LM Studio / Ollama 等，内置 8 家厂商预设）。

---

## 功能特性

### 核心分析能力

- **全书级逐章分析**：三阶段并发流水线（串行预热 → 流式并发 → 失败补跑），每章产出 9 类结构化数据（核心事件、人物弧光、伏笔、剧情漏洞、地点与空间关系、跨块呼应、知识更新、长上下文洞察）
- **结构化滚动总结（Rolling Summary）**：后台独立 LLM 任务增量维护全书记忆——时间轴、范式层、里程碑、因果链、近期势头四层结构，长书不断章记忆
- **最终总结（4 阶段）**：分卷摘要+伏笔调和 → 全书伏笔复检 → 写作风格分析 → 全书脉络报告，产出 `final_summary_report.md` + `foreshadow_audit.md`
- **伏笔 50 类分类体系**：prompt 源头约束 + per-book 映射兜底双保险，伏笔总表支持重要度/置信度/类别三维过滤、语义去重、分层截断
- **写作风格分析**：22 项统计指标（句长/对话占比/TTR/感官密度…）+ LLM 8 维语义特征（叙事节奏/对话风格/修辞偏好…）

### 工程可靠性

- **断点续跑**：章节分析与最终总结两级 checkpoint。分析中断重跑跳过已完成块；总结中断后卷摘要/调和结果复用，只补缺的调和；复检阶段每批落盘 ledger，已回收伏笔不重复付费
- **三层容错链**：LLM 重试（温度退火 → 指数退避）→ JSON 容错解析（8 策略，含漏引号定向修复）→ 失败块 3 轮补跑
- **零数据丢失哲学**：失败 token 不计零、卷摘要/调和独立落盘、KB 自动备份（保留 10 份）、断电崩溃可恢复
- **成本优化**：prompt 按变化频率排序形成稳定前缀最大化远端 KV cache 命中；伏笔调和省 token 规则（未回收只输出 ID）；失败块复用缓存
- **实时进度**：WebSocket 广播日志/进度/token 统计（含 KV 缓存命中率），桌面端秒级停止

### 可视化与工具

- 时间线（事件泳道 + 伏笔埋设/回收 + 50 类筛选）、角色关系图、地点地图、角色卡（HTML/文本）
- 小说切分器（13 种章格式自动识别、卷/部层级、广告清理、防盗章去重、超长章拆分、批量处理）
- 数据聚合（11 类 JSON）与 Excel 多 Sheet 导出
- Prompt 预览（调试真实 LLM prompt）、Token 统计面板

---

## 快速开始

### 环境要求

- Python 3.12+（开发验证于 3.13）
- Node.js 18+（仅前端构建需要）
- 一个 OpenAI 兼容的 LLM API Key

### 1. 安装后端依赖

```bash
pip install -r requirements.txt
```

### 2. 构建前端

```bash
cd frontend
npm install
npm run build      # 产物输出到 frontend/dist
```

或直接双击 `build_frontend.bat`（在本地磁盘构建后自动把 dist 同步回共享盘，规避 esbuild 网络盘限制）。

### 3. 配置 API

启动后在 **设置页** 填写 `Base URL` / `API Key` / `模型`（可从预设下拉一键填充，如 `https://api.minimaxi.com/v1` + `MiniMax-M2.7`）。

> API Key 也支持环境变量 `LLM_API_KEY` / `MINIMAX_API_KEY` 注入，优先级高于配置文件。

### 4. 启动

**桌面模式**（推荐）：

```bash
run_desktop.bat
```

pywebview 自动完成：找空闲端口 → 后台起 uvicorn → 轮询 `/api/health` 就绪 → 打开 1600×900 原生窗口。中文/网络盘路径的 WebView2 限制通过"本地运行时副本 + `NOVEL_ROOT` 指回共享盘"解决。

**开发模式**（前后端热重载）：

```bash
run_dev.bat
# 或手动：
python -m uvicorn backend.app:app --reload --port 8000   # 后端 http://localhost:8000/docs
cd frontend && npm run dev                                 # 前端 http://localhost:5173
```

---

## 使用流程

```
1. 小说切分页   —— 导入 txt，自动识别章节/卷，清理广告，批量切分到 workspace/《书名》/blocks/
2. 分析队列页   —— 扫描工作区入队 → 开始分析（实时进度/日志/token 统计）
3. 最终总结页   —— 选书 → 4 阶段总结 → 报告/审计/伏笔账本
4. 可视化页     —— 时间线 / 关系图 / 地图 / 角色卡
5. 统计面板     —— token 消耗、缓存命中率、重试成本
```

### 输出目录结构

```
workspace/
├── 《书名》/                      # 分析工作区
│   ├── blocks/                   # 切分产物（0001.txt + chapters.json + metadata.json）
│   └── output/
│       ├── chapter_N_result.json          # 逐章分析结果（含 raw_response）
│       ├── novel_analysis_aggregated.json # 全书聚合
│       ├── final_summary_report.md        # 最终总结报告
│       ├── foreshadow_audit.md            # 伏笔审计报告
│       ├── foreshadow_ledger.json         # 伏笔账本（active/resolved/dormant）
│       ├── foreshadow_type_map.json       # type→50类 映射（旧书兜底）
│       ├── style.md                       # 写作风格分析
│       ├── aggregated/                    # 11 类聚合 JSON
│       └── final_summary_checkpoint/      # 总结断点（volume_N.md / recon_N.json / manifest.json）
└── 分析结果/《书名》/              # 归档区
```

---

## 核心机制

### 章节分析流水线（pipeline.py）

1. **块划分**：按实际存在的章号切块（容错断号目录），块大小可配
2. **串行预热**：前 N=并发数 块顺序分析，建立初始知识库（保证后块读不到未来章节知识）
3. **流式并发**：`asyncio.Semaphore` 限流 + `as_completed` 并发分析，每 N 批 checkpoint 落盘
4. **失败补跑**：最多 3 轮串行重试失败块
5. **滚动总结**：后台独立任务，跨阈值触发（短书按总章数 1/3 自适应），增量更新四层 JSON；里程碑 FIFO 淘汰但锁定首条与每范式层首条；近期势头满窗触发 LLM 归档压缩

### LLM 客户端容错（llm_client.py）

- **单次调用三重竞争**：API 请求 / 用户停止 / 硬超时（SDK timeout+30s），实现秒级停止
- **两级重试链**：温度退火（`temp = max(0, 初始 - 尝试次×步长)`）→ 指数退避（最多 2^N 秒）；认证失败立即终止
- **验证回调**：响应先过 `validate_response`（线程池运行），失败时在 user 消息尾部追加格式修正提示重试
- **思考模式控制**：`thinking_mode` 经 extra_body 注入，兼容 `{"thinking":{"type":"disabled"}}`（mimo/GLM/M3）与 `{"enable_thinking":false}`（DeepSeek/Qwen）；响应侧自动剥除 `<think>` 标签与 `reasoning_content`
- **KV cache 统计**：读取厂商扩展字段统计缓存命中 token，前端可查命中率

### JSON 容错解析链（json_utils.py）

8 级策略：直接解析 → 截尾 → 去注释 → 漏引号定向修复（两轮，先于 lenient 解析器）→ json5 → `ast.literal_eval` → json-repair → 单引号值转双引号。

### 伏笔系统（50 类分类）

- `constants.py` 定义 50 类功能分类（8 组：人物/情节/冲突/关系/设定/主题/题材/其他），每类附 2-3 个 type 锚点，schema 版本号管理
- **源头约束**：分析 prompt 内嵌分类表，要求 `type` 字段必须从中选择、禁止自创
- **被动兜底**：旧书自由式 type 经 per-book `foreshadow_type_map.json`（含 schema 校验）映射到 50 类，零 LLM 调用
- **伏笔总表**：收集 → 分类 → 三维过滤（重要度 ≥ 最低 / 置信度 ≥ 最低 / 类别 ∈ 保留集）→ 语义去重（关键词倒排 + SequenceMatcher ≥0.6）→ 综合排序 → 分层截断（高 500 / 中 200）
- **伏笔账本**：状态机 active/resolved/dormant（休眠判定：15 批未现且跨度 ≥200 章），reconciliation 逐批判定回收，全书复检兜底
- **时间线联动**：TimelinePage 按 50 类 chip 多选筛选，设置页可勾选保留类别与重要度/置信度阈值

### 最终总结 4 阶段（final_summary.py）

| 阶段 | 内容 | 断点 |
|---|---|---|
| 1/4 分卷分析+伏笔调和 | 每批两次 LLM：Markdown 卷摘要 + JSON 回收判定 | ✅ 卷摘要一完成即落盘；调和失败只补调和 |
| 2/4 全书伏笔复检 | 剩余 active 伏笔按 40 个/子批集中复检，每批携带全量卷摘要（依赖 KV cache 复用前缀） | ✅ 每批完成立即落盘 ledger，重启只复检剩余 active |
| 3/4 风格分析 | 统计指标 + LLM 8 维语义，注入最终报告 | ❌ 成本低，重跑无所谓 |
| 4/4 最终报告 | 卷摘要（超阈值分层压缩）+ 伏笔上下文 + 审计 + 风格 → `final_summary_report.md` | ❌ 单次调用 |

**总结专用模型/思考模式**：总结页可独立选择模型（`summary_model`，复用同一 base_url/api_key，重型任务可切 M3 等）与思考模式（`summary_thinking_mode`），空值跟随全局设置——解决思考型模型在大 prompt 上延迟过高的问题。

---

## 配置说明（config.json）

`api` 段：

| 字段 | 默认 | 说明 |
|---|---|---|
| `base_url` / `api_key` / `model` | 预设空 | 任意 OpenAI 兼容端点；key 可用环境变量覆盖 |
| `max_tokens` | 130000 | 输出上限（>65537 时警告） |
| `timeout` / `summary_timeout` | 300 / 600 | 分析 / 总结阶段 API 超时（总结 prompt 11 万字符级） |
| `json_mode` | default | default/qwen/deepseek/glm47 |
| `temperature` / `temperature_step` | 0.5 / 0.25 | 温度退火 |
| `temperature_max_retries` / `backoff_max_retries` | 2 / 1 | 重试链 |
| `thinking_mode` | {} | 思考控制（mimo/GLM/M3 用 thinking，DeepSeek/Qwen 用 enable_thinking） |
| `summary_model` | "" | 总结专用模型，空=跟随 model |
| `summary_thinking_mode` | {} | 总结专用思考控制，空=跟随 thinking_mode |

`analysis` 段（节选）：`concurrency`、`block_size`、prompt 预算（`max_arcs_in_prompt` 等）、`foreshadow_kept_categories`（50 类保留集）、`foreshadow_min_importance/confidence`、`max_foreshadow_catalog_high/mid`、`volume_compress_threshold/group`（报告卷摘要压缩）、滚动总结参数、`auto_summary`/`summary_concurrency`/`summary_batch_size`、`auto_archive`、`checkpoint_interval`。

---

## API 概览（前缀 `/api`）

| 分组 | 端点 | 用途 |
|---|---|---|
| 分析 | `POST /analysis/start` `POST /analysis/stop` `GET /analysis/status` `GET /analysis/token_stats` | 队列分析控制与统计 |
| 队列 | `GET/PUT /queue` `POST /queue/scan` `/scan_workspace` `/remove` `/move_up|down` `/delete_book` `/reset_item` `/clear` | 队列管理（删除=移入回收站） |
| 书目 | `GET /books` `GET /books/{id}/results|report|ledger|characters|chapter/{n}` | 书目与结果查询 |
| 总结 | `GET /summary/status` `POST /summary/start` `POST /summary/stop` | 最终总结 |
| 风格 | `POST /style/start` `GET /style/status` `GET /style/result/{id}` | 风格分析 |
| 可视化 | `GET /viz/timeline/{id}` `GET /viz/graph/{id}` `GET /viz/map/{id}` | 时间线/关系图/地图 |
| 聚合 | `POST /aggregate/run` `GET /aggregate/{id}/files|file/{name}` `POST /aggregate/{id}/excel` | 聚合与导出（文件读取有路径穿越防护） |
| 切分 | `POST /splitter/preview|save|batch|infer_name` | 小说切分 |
| 设置 | `GET/PUT /settings` `GET /settings/presets|models` `POST /settings/preview/models` | 配置（运行中 PUT 拒绝） |
| 伏笔 | `GET /foreshadow/categories` | 50 类定义+保留集+阈值 |
| Prompt | `POST /prompt/preview` | 真实 prompt 调试 |
| 工作区 | `GET /workspace/novels|archives` `POST /workspace/archive|archive_all|delete_archive` | 归档管理 |
| WS | `/ws/progress` | 实时日志/进度/token 广播（消息类型：log/progress/block_done/state_change/token_stats/summary_progress） |

---

## 测试

```bash
python -m pytest        # 55 个用例，backend/tests/
```

覆盖：LLM 重试链全场景（温度退火/认证即停/停止/硬超时）、JSON 容错修复、模型序列化、队列状态机、自动总结护栏、**总结 checkpoint 断点续跑**（恢复零 LLM 调用、batch_size 变更作废、调和缺失只补调和）、知识库快照隔离（低章号读不到未来章节）、断号目录、伏笔去重。

---

## 目录结构

```
├── desktop.py                    # pywebview 桌面入口（本地副本+NOVEL_ROOT 双目录方案）
├── backend/
│   ├── app.py                    # FastAPI 应用工厂（路由挂载/静态托管/日志轮转）
│   ├── main.py                   # 极简启动器
│   ├── progress_hub.py           # WebSocket 进度广播中枢
│   ├── api/                      # 11 组 REST 路由 + ws.py
│   ├── core/
│   │   ├── pipeline.py           # 全书分析流水线（预热/并发/补跑/滚动总结）
│   │   ├── analyzer.py           # 单章分析引擎（验证回调/重试提示）
│   │   ├── llm_client.py         # LLM 客户端（重试/退火/思考控制/KV 统计）
│   │   ├── prompt_builder.py     # system prompt（10 规则 + 伏笔 50 类 + KV cache 布局）
│   │   ├── knowledge_base.py     # 磁盘知识库（增量合并/备份）
│   │   ├── memory_state.py       # 运行期内存/断点续跑
│   │   └── style_analyzer.py     # 风格分析（22 统计项 + 8 维语义）
│   ├── services/
│   │   ├── queue_service.py      # 队列分析编排（单例/自动总结/状态持久化）
│   │   ├── summary_service.py    # 总结编排层
│   │   ├── final_summary.py      # 总结执行器（4 阶段 + checkpoint + 伏笔总表）
│   │   ├── book_service.py       # 书目注册/状态
│   │   ├── splitter_service.py   # 小说切分
│   │   └── viz_service.py        # 可视化数据聚合
│   ├── config/                   # constants.py / settings.py（dataclass）/ presets.py
│   ├── models/                   # AnalysisResult / KnowledgeBase 数据模型
│   ├── utils/                    # json_utils / text_utils / aggregate_utils / excel_export / character_card_generator 等
│   └── tests/                    # 55 个测试
├── frontend/
│   ├── src/
│   │   ├── pages/                # 12 个页面（队列/总结/设置/时间线/关系图/地图/角色卡…）
│   │   ├── components/           # BookSelector / ProgressBar / LogConsole / ChapterDetailPanel…
│   │   └── api/                  # client.ts（53 个方法）+ useProgressSocket.ts（WS 单例）
│   └── package.json              # Vue3 + Vite + Tailwind + TS
├── config.json                   # 运行配置（不提交密钥版本）
├── queue_state.json              # 队列状态持久化
├── run_dev.bat / run_desktop.bat / build_frontend.bat
└── requirements.txt
```

---

## 日志与排障

| 文件 | 内容 |
|---|---|
| `analyzer.log`（10MB×3 轮转） | 主日志：LLM 调用/token/流程/错误 |
| `api_failures.log` | LLM 调用失败明细（24h 滚动）：温度/错误类型/等待时长 |
| `crash.log` | 桌面端未捕获异常 / pywebview 原生报错 |
| `%LOCALAPPDATA%\NovelAnalyzer\run.log` / `build.log` | 桌面启动与前端构建诊断 |

**常见问题**：
- **分析慢/超时**：`api.timeout` / `api.summary_timeout` 调大；重试链 `temperature_max_retries`/`backoff_max_retries` 调小快速放弃；总结页单独切更快的模型
- **最终总结卡住**：查看 `api_failures.log` 的错误类型分布；思考型模型（M2.7 等）无法关闭思考，可切 M3（`thinking:disabled`）或换快模型
- **前端改了不生效**：运行 `build_frontend.bat` 重新构建 dist，再重启应用（后端新配置字段需重启加载）

---

## 开发说明

- 开发模式前后端分离（vite 5173 + uvicorn 8000，CORS 已放行）
- 改动后端后重启进程生效；改动前端后 `npm run build`
- 新配置字段：`APIConfig`/`AnalysisConfig` dataclass 加字段即自动读写（`_filter_fields` 保证旧 config.json 兼容），前端 `AppConfigDto` 同步补类型
- Prompt 结构调整注意 KV cache 布局原则：变化频率低的块（分类表/世界观/主题）进 system 稳定前缀
