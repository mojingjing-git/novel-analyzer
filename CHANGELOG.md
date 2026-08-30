# 小说智能分析器 — 完整更新日志

> 覆盖从 2026-03-03 首个雏形脚本到当前 Web 正式版（F:\AI\小说分析器）的全部历史版本。
> 历史版本存档路径：`E:\AI\小说`（雏形期，2026/3）→ `F:\tool\novel_analyzer`（PyQt6 桌面版，2026/4-6）→ `F:\AI\微型集群`（PySide6 迭代期，2026/6-7）→ `F:\AI\小说分析器`（Web 正式版，2026/8 至今）。

---

## 阶段总览

| 阶段 | 时间 | 版本形态 | 技术栈 | 存档位置 |
|---|---|---|---|---|
| 一、雏形期 | 2026-03-03 ~ 03-18 | 单文件脚本 → 四合一工作台 | tkinter + OpenAI SDK（Qwen 系） | `E:\AI\小说` |
| 二、PyQt6 桌面版 | 2026-04-15 ~ 06-01 | PyQt6 桌面应用（Git 化） | PyQt6 + openai SDK | `F:\tool\novel_analyzer` |
| 三、PySide6 迭代期 | 2026-06-14 ~ 07-29 | v0.5 → v0.9.x → 多分支 → Web 原型 | PySide6 → FastAPI+React | `F:\AI\微型集群` |
| 四、Web 正式版 | 2026-08-01 ~ 至今 | FastAPI + Vue3 + pywebview | FastAPI + Vue3 + Tailwind | `F:\AI\小说分析器` |

---

## 阶段一：雏形期（2026-03-03 ~ 03-18）`E:\AI\小说`

### 2026-03-03 ｜ 项目源头：单块 LLM 分析雏形（`1/` 目录）

- 首个脚本 `run.py`（404 行 GUI 版）与更原始的纯命令行版本（`无明显问题会自动重置版.txt`，174 行）
- 核心思想从第一天起就没变过：**把小说按块喂给 LLM，强制输出 JSON 结构化分析（核心事件/人物弧光/伏笔/逻辑漏洞/跨块呼应/知识更新），用 `knowledge.json` 做跨块记忆**
- 本地 **LM Studio qwen3-8b** 推理，OpenAI 兼容接口；每 20 块触发主线压缩
- 实际分析《死人经》405 块；`compressed_arcs` 里残留 `<think>` 思考痕迹（早期清洗 bug 见证）

### 2026-03-05 ｜ 多模型分支与聚合闭环

- **`deepseek1` / `ver.1-20260304`**：同一脚本（MD5 相同）交给 DeepSeek 重写整理，API 转向阿里云 DashScope（qwen3-coder-flash）；新增**长上下文模式**与「洞察」输出（跨章模式/伏笔网络/节奏）
- **`kimi1`**：Kimi 独立实现版，新增**聚合闭环**：分析 → 聚合报告（AnalysisAggregator）→ 精简（JSON Optimizer Pro）→ LLM 输入；`0.0.1聚合.py` 指纹去重合并工具
- 本地 LM Studio 版（qwen3.5-9b）与云端版并行验证

### 2026-03-07 ｜ 三小件工具 + ver.1.1 系列

- **`ver.1.1-20260307`**：长上下文模式正式化（max_arc_length=2000）；`ver1.1-fork`（本地 9B 对比测试）；**`ver1.1-fork2`：结构化世界观记忆**——初始化并回写 10 个知识库字段（world_building/long_term_arcs/foreshadowing_network/character_relationships/thematic_elements/pacing_tracker），从"只记时间线+摘要"升级为结构化记忆
- **`拆书小软件`**：不调 LLM 的 txt 章节切分工具（多编码检测 GBK/Big5、13 种章节关键词、按目标字数分块）
- **`聚合去重小软件`**：按块号合并去重、角色关系推导、Markdown 报告
- **`角色卡小软件`**：从聚合结果提取全部角色 → 事件/弧光/关系统计 → 标准 Markdown 角色卡 + 索引（实战：《三侠五义》300+ 角色卡）

### 2026-03-08 ~ 03-18 ｜ 四合一工作台（雏形期收官）

- **`完整工作流/release-1.0.0`**：分割 → 拆解（分析）→ 汇总 → 人物卡四件套流水线
- **`工作台v1.0.0`**：单文件四合一统一工作台（1995/2422 行，NovelSplitter/NovelAnalyzer/AnalysisMerger/RoleCardGenerator 集成进一个 tkinter 界面）
- **`工作台v1.0.1`**（3/18 最后修改）：配置切到本地 unsloth/qwen3.5-9b，雏形期结束

**本阶段定型的核心机制（沿用至今）**：OpenAI 兼容接口、JSON 强约束 prompt、温度退火重试（0.1→0.05→0.0）、knowledge.json 跨块记忆、思考链剥离。

---

## 阶段二：PyQt6 桌面版（2026-04-15 ~ 06-01）`F:\tool\novel_analyzer`

### 2026-04-15 ~ 04-28 ｜ 从脚本到正式应用

- 4/15：《三侠五义》121 章 txt 就绪；4/18 首次运行（E 盘），LM Studio 本地模型，串行逐章分析 55 章，两阶段重试机制上线
- 4/22：`RETRY_MECHANISM.md`（温度退火 0.1→0.05→0.0 + 指数退避 2^n 秒）、命令行聚合 CLI
- 4/25-27：小说切分工具 CLI → **PyQt6 GUI 版**（`cuttttttt*.py`，编码探测/分词）
- 4/28：**正式定型**——requirements、main.py、README、应用图标、PyInstaller spec（`novel_analyzer.spec`，应用名"小说智能分析器"）；完成 64 章聚合 + characters.xlsx 导出；项目从 E 盘迁至 F 盘

### 2026-05-07 ｜ Git 化与首轮大修

- 建立 git 仓库（`.gitignore` 排除 workspace/日志/产物）
- **首提交即修复 14 个 bug**：GUI 闪退、数据反序列化、路径硬编码等（`f78048e`）

### 2026-05-16 ｜ 功能爆发周（git 可考）

- **block_size 功能**：多章合并为一个分析块（每块 N 章），块 ID 取首章号
- **伏笔回收机制**：`active_foreshadowing` 带编号注入 prompt，`resolved_foreshadowing` 显式回收，回收后不再截断增长
- 评审反馈修复：子串匹配阈值调优（10→6 字符）、去魔法数、补 debug 日志

### 2026-05-17 ｜ 最终总结页上线

- **FinalSummaryPage**：批量 LLM 分析全书脉络（分卷摘要 + 全书报告），配套修复：`json_mode=default` 强制（DeepSeek json_object 模式坑）、报告/卷摘要自动保存、章节按数值排序
- **并行模式实绩**：半小时完成《三侠五义》全书 121 章分析（串行预热 + 分片并行 + 断点续跑 + 失败 3 轮补跑）
- GUI 五个主标签页成型：分析结果 / 数据聚合 / 角色卡生成器 / 最终总结 / 统计面板；顶部 Prompt 预览、小说切分、工作区管理、风格分析入口
- 12 维度数据聚合 + Excel 多 Sheet 导出 + 角色卡（HTML/MD/TXT）

### 2026-05-27 ~ 06-01 ｜ 创作辅助转型（未提交的开发）

- 核心模块二次大改：KB 截断上限、llm_client、analyzer、json_utils 等 8 文件（**未提交 git**）
- **`blueprint_generator.py`**：全书结构蓝图生成（GUI）
- **`workspace_manager.py`**：多书归档管理器（GUI）
- **`style_analyzer.py` v2**：代码统计 + LLM 七维评分（叙事距离/句法节奏/感官密度/词汇层级/比喻策略/情感表达/对话比重）
- **`batch_style_v2.py` + `show_comparison.py`**：5 本书批量风格对比（三侠五义/圣墟/大王饶命/永夜君王/超神机械师）
- **skills/ 创作工作流技能**：拆书 → 蓝图 → 世界观 → 角色映射 → 新书生成 → 验证（从"分析工具"向"创作辅助平台"演进）

---

## 阶段三：PySide6 迭代期（2026-06-14 ~ 07-29）`F:\AI\微型集群`

> 技术栈统一迁移到 **PySide6**，多分支并行开发；此阶段确立了最终总结 4 阶段、伏笔账本、滚动总结、内存化、队列、Web 化等全部关键能力。

### v0.5 稳定版 ｜ 基础分析器定型

- "六路上下文注入"（故事历史/前情摘要/时间线/角色状态/世界观/伏笔网络）
- 分片队列并发（按并发数分桶）、断点续跑、失败补跑 3 轮、温度退火、思考链过滤
- 最终总结分批 + 汇总、数据聚合/Excel/角色卡/风格提取（单层 LLM 七维评分）
- 产物三件套格式：`chapter_N_result.json` + `chapter_N_data.json` + `chapter_N_report.md`（含冗余写盘，后续优化点）

### v0.6 ｜ 测试体系 + 伏笔账本

- **"九路上下文注入"**：新增角色关系/已验证事实/主题元素
- **tests/ 测试体系**（11 个测试文件）
- **`utils/foreshadow_ledger.py` 伏笔账本**（active/resolved/dormant 状态机）与伏笔审计报告
- 滚动总结以"批次 Rolling"初版形态出现；最终总结并发批次设计文档（06-16）
- 吞吐率/剩余时间预测、完整 API 配置

### dev 主干（git 05-07 ~ 06-24）｜ v0.7 → v0.9.0 → v0.9.1

| 日期 | 里程碑 |
|---|---|
| 06-17 | 并发批处理 + 伏笔账本 |
| **06-18** | **批次 Rolling Summary + KV Cache 优化**（prompt 分层：早期固定 + 近期滚动） |
| **06-20** | **v0.9.0 性能优化 + 结构化滚动总结 + 全量测试覆盖**；v0.9.1 修复 10 个 bug |
| 06-21 | **风格提取两层架构**（14 项统计硬指标 + 8 维 LLM 语义） |
| 06-22~24 | 设置界面完善（双主题 one_dark/one_light） |

### 07-07 ｜ UI 分支：win11 风格

- **Fluent Design 双主题**（themes/fluent.qss + fluent-dark.qss）
- **伏笔账本图形页**（gui/foreshadow_page.py）、设置对话框（滚动总结触发章数）、模型查询弹窗
- 毛玻璃效果原型（liquid_glass_demo.html）

### 07-16 ｜ 桌面版打包交付

- **构建版 0.0.1**：PyInstaller 成品「小说智能分析器.exe」（qwen2.5-72b-instruct-1m / DashScope）

### 07-21 ｜ 内存化 IO 优化（关键性能里程碑）

- 设计文档：《MemoryState 全内存 IO 优化设计》（实测每章 IO 开销 30-50s，目标 <1ms）
- **`core/memory_state.py`**：全部 AnalysisResult + KnowledgeBase 常驻内存，threading.Lock 并发安全，checkpoint_interval（默认 5 批）落盘，KB 快照子集缓存，删除 data.json 冗余写
- **流式并发池**替代批次 barrier 同步（提交：`feat: MemoryState 全内存 IO 优化 + 流式并发池`）

### 07-26 ~ 07-29 ｜ 多书队列 + Web 化（架构转型）

- **`core/queue_manager.py`**：多小说顺序分析队列（QueueItem 状态机 pending/running/done/failed/skipped、queue_state.json 持久化、check_api 预检）；实跑《卡徒》514 章（block_size=2）
- 07-28：内嵌 Web 工程副本 + 打包成品 NovelAnalyzerWeb.exe
- **07-29 `novel_analyzer_web_dev`**：**Web 版诞生** —— FastAPI（app.py 工厂 + 8 组路由 + ws.py WebSocket + progress_hub 进度广播）+ **React 19 + TypeScript + Vite** 前端（14 个页面）+ pywebview 桌面壳 + PyInstaller spec

---

## 阶段四：Web 正式版（2026-08-01 ~ 至今）`F:\AI\小说分析器`

> 当前版本。从 web_dev 快照起步：前端从 React 重写为 **Vue 3 + Tailwind 4 + TypeScript**（体积更小、与桌面壳集成更顺），后端在 FastAPI 架构上持续演进。

### 08-01 ｜ Git 初始化 + 畸形 JSON 修复 + 重试成本可见

- `init: 小说分析器代码快照`（含设计文档），GitHub Actions CI 配置
- **json 漏引号定向修复** + `parse_json_robust` 诊断信息（8 级容错链：直接解析 → 截尾 → 去注释 → 漏引号修复 → json5 → ast → json-repair → 单引号转换）
- **重试成本可见**：llm_client 累计 total_attempts → analyzer → pipeline → token_stats → 统计面板展示重试次数与失败 token 成本（"API 已扣费不能归零"哲学）；重试归因改按调用统计（并发准确）、耗时冻结、失败章节计入统计
- 12 个提交完成本批（设计 → 计划 → 实现 → 修正闭环）

### 08-02 ｜ 队列自动总结 + 书目标记（spec：auto-summary-and-book-marks）

- **队列完成后自动总结**：所有书分析完成后逐本串行执行最终总结（`auto_summary` 配置 + `allow_during_analysis` 护栏——仅自动总结可绕开"分析期间禁手动总结"）
- 总结批次/并发参数持久化到 config.json（取代 localStorage）
- 书目标记：书目状态摘要（has_report/has_ledger/has_audit/has_aggregated）、工作区扫描自动发现新书

### 08-07 ｜ 性能与正确性优化批（QUA/PERF/P0 系列）

- **P0-1 知识库快照隔离**：KB 快照子集缓存，低章号块读不到未来章节知识（并发正确性）
- **P0-2 休眠判定统一收尾**：批次并发使 last_seen_batch 非单调 → 全部批次完成后按批次升序统一 reconcile
- **P0-4 断号目录**：块划分按实际存在的章号（容错缺失章）
- **PERF-2 伏笔去重倒排索引**：关键词倒排 + SequenceMatcher ≥0.6 精筛
- **QUA-1 卷摘要压缩**：最终报告卷摘要超阈值（8 万字符）分层压缩（首尾组保留全文、中间组截半）
- **PERF-1 风格统计单次正则**：8 组词表合并单条正则一次 finditer（避免约 120 次全文扫描）
- 测试补全：快照缓存不泄漏未来章节、断号目录、休眠判定、去重倒排语义

### 08-09 ｜ 伏笔 50 类分类体系（治本）

- **50 类功能分类定义**（`FORESHADOW_CATEGORY_DEFS`，8 组 50 类，schema 版本号管理，兜底"其他"）
- 先落地 **LLM 归一化兜底版**（实测教训：单批 >100 条质量退化、思考链吃光 max_tokens、每批落盘防崩溃丢进度），《北宋穿越指南》497 章全量验证：1663 种 type 全量映射、0 幻觉、700 条伏笔总表、2786 条时间线全分类
- **随后改为源头约束主线**：分析 prompt 内嵌 50 类表（恒定文本注入 system 前缀，KV cache 友好），`type` 字段禁止自创；归一化版作为分支备份（`F:\AI\小说分析器 - 归一化兜底版`）
- 旧书靠 per-book `foreshadow_type_map.json`（含 schema 校验）被动兜底，零 LLM 调用
- 配套：伏笔总表三维过滤（重要度/置信度/类别）+ 分层截断（高 500/中 200）；时间线 50 类 chip 筛选；设置页类别勾选；`GET /api/foreshadow/categories`

### 08-09 ｜ 总结阶段独立模型选择（应对大模型延迟问题）

- 诊断：M2.7 思考模型在 50-110K 字符大 prompt 上延迟 5-15 分钟，630s 硬超时卡在延迟中位数 → 大量请求被误杀（实测记录：M2.7 官方 issue 报告平均响应 ~650s；**非安全审核**——涉敏错误码 1026/1027 会秒回错误响应，而全部失败都是"API 无响应"）
- **`summary_model` + `summary_thinking_mode`**：总结页独立模型下拉（复用同一 base_url/api_key，重型任务切 M3 等）+ 思考模式三档（自动 / mimo·GLM·M3 禁用思考 / DeepSeek·Qwen 禁用思考），持久化到 config.json
- **实测效果**：切 `deepseek-v4-flash` 后最终报告 3 分 21 秒一次通过（167K tokens），对比 M2.7 卡 630s×3 轮仍失败
- `summary_timeout` 默认 600s 与 max_tokens 告警阈值（64K→65537）微调

### 08-09 ｜ 最终总结断点续跑补全

- 阶段 1/4 卷摘要断点此前已有（volume_N.md 一完成即写 + recon_N.json 失败不写 → 重启只补调和 + manifest 校验 batch_size/ch 范围）
- **新增阶段 2/4 复检续跑**：每完成一批复检立即落盘 ledger（进 `_lock` 防并发写竞争），崩溃/停止后重启只对仍 active 的伏笔复检，已回收的不重复付费
- 配套：README 完整文档

### 08-24 ｜ 双批代码审计 + 14 个 P1 修复

- 第一批 5 路并行审计报 66 条 → 第二批 5 路对抗复核（53 确认 / 12 部分成立降级 / 1 驳回），落地全部 14 个 P1：滚动总结 KB 快照注入分析 prompt、pipeline rolling 容错、llm_client APIError 嗅探防崩穿重试链、final_summary 断点 results 指纹失效 + stop 级联取消风格任务、归一化全空判败不落盘/空批熔断/缓存短路要求非空、delete_book 显式路径防同名误删、切分保存运行护栏 + 暂存-交换原子写、settings from_dict 数值强转 + 损坏 config 修复前拒绝 save 保 API Key、前端 GraphPage ECharts 死 DOM 自动重绑 + Timeline/CharacterCard/Summary 三处切书守卫
- 逐项明细见计划文档：`docs/superpowers/plans/2026-08-24-p1-bug-fixes.md`

### 08-24 ｜ P2/P3 划算项批量修复（S+A 两档 19 项）

- 后端 10 处：pipeline 快照构建线程池化 + failed 按章号范围过滤 + 势头 join 容错；models 字符串数组字段整串收编 + validate 嵌套对象类型检查；json 修复链全策略 dict 守卫 + safe_save_json 进程内唯一 tmp 名；聚合主文件改 safe_save_json 原子写；queue put_queue 畸形条目跳过 + 停止后不再广播假 done；final_summary run() 阶段段 try-finally 兜底回收 style_task；workspace 回收站 aborted 视为失败 + 归档回滚失败记录滞留路径；excel 零 sheet 明确报错 + 单元格公式注入消毒；moderation 瞬时误判多给一轮温度尝试；prompt_builder 章节正文超限尾部保序截断
- 前端 9 处：graph tooltip 转义 + loadData 切书快照守卫；summary viewAggFile 切书快照守卫；chapter loadChapter 早退分支失效在飞响应；map 归一化结束沿 getMap 补 try-catch；pywebviewready 监听 + rAF 卸载取消 + 日志 id 过滤；queue-page 定时器分键清理 + 删除确认改名称快照；api client 路径参数统一 encodeURIComponent；markdown 链接占位保护防强调正则污染 href；settings 提交前剪除空字符串数值字段
- 验证：后端全量 pytest 226 passed（本批新增约 15 用例），前端 vue-tsc + vite build 通过
- **未动**：KB 近邻指纹、切分窄化算法、协议探测回退等高成本项；逐项明细见计划文档：`docs/superpowers/plans/2026-08-24-p2p3-batch-fixes.md`

### 08-24 ｜ 深度修复批次（G1-G7，大改值得七项）

- 上批「未动」的三项高成本项全部落地：KB 近邻指纹（G2）、切分窄化算法（G6）、协议探测回退（G5），外加四个审计高优先项
- 后端 9 文件：memory_state 审核拦截 skipped 标记随 checkpoint 持久化 + 成功落盘清理陈旧标记 + pipeline 续跑过滤已 skipped 块（重启不再重付 LLM 重试链费用）；knowledge_base 近邻增量校验 ≤best_limit 基线区间文件指纹、失效即全量重建，命中产物返回副本断开对象别名（陈旧 KB 不再固化进最终 knowledge.json）；style_analyzer `token_sink` 经 final_summary 接入 runner 统计（style 分类恒零盲区消除）；book_service `get_book_path` miss 改后台单飞刷新并立即返回 None（冷启动保留一次阻塞刷新，消除事件循环秒级冻结）；queue_service 预检加跨协议探针自动纠正 provider 误判（OpenAI 网关上的 claude-* 不再整书 404）；splitter 次级章节格式按比额门槛并入切分（绝对得分 ≥2 且 ≥ 主导的 15%，卷章混排书不再吞章、高频编号列表仍被挡住）；text_utils 编码检测改采样罚分择优（U+FFFD×8/控制字符×4/PUA×3 除以样本长度取最低罚分，Big5 繁体书不再坠入 gb18030 乱码，`detect_and_decode`/`detect_encoding` 两入口预检统一）
- 新增测试文件 7 个：test_memory_state_skipped / test_kb_fingerprint / test_style_tokens / test_book_service_async_refresh / test_provider_heal / test_splitter_mixed_formats / test_text_encoding
- 验证：后端全量 pytest 251 passed（本批新增约 24 用例），前端 vue-tsc + vite build 通过

### 08-24 ｜ 前端 vitest 基建 + P3 清扫

- 测试基建：引入 vitest + jsdom + @vue/test-utils 与独立 vitest.config.ts（markdown 自运行脚本迁入 npm test，新增 useLogStore/useProgressSocket/TimelinePage/client 4 个守护测试文件，全量 34 用例）；P3 修复 3 处：client.ts 非 JSON 的 200 响应显式抛错不再静默返回空对象、viewAggFile 过期失败不再污染新书 aggError、GraphPage 占位项清除上一本书的 loading 残留
- 验证：npm test 全量 34 用例通过（vitest+jsdom），vue-tsc + vite build 通过；后端零改动

### 未提交清单（当前工作区）

- ~~08-02 之后的所有里程碑（自动总结/优化批/伏笔 50 类/模型选择/复检续跑）尚未 git 提交~~ 已全部正式提交（08-27 批次，至 `46bc715`），本节作废
- ~~系统备份：`F:\AI\小说分析器 - 归一化兜底版`（08-09，伏笔归一化分支完整快照）~~ 已于 2026-08-31 删除（连同 `小说分析器 - 副本`，共 690MB）：基线 cae160a 在主仓历史内、两份备份的全部改动文件均已被后续正式实现覆盖，验证后清理；`代码版本/`（4GB，git 化之前的手动版本档案）与 `数据产物/`（2.3GB，分析结果归档）保留

---

## 2026-08-26 队列运行中实时仪表盘（H17 / S1）

- **后端（queue_service.py:700 on_progress）**：
  - 新增 `block_start` WS 消息（status=start，带 chapter/range/progress/total/ts）
  - `block_done` 补 `range` 字段
  - 新增 `discovery` 消息（status=done + result 存在）：core_events 数 / foreshadows 截 24 字 / 新人物（首次登场 + 停用词过滤）/ unresolved 截 24 字
  - 顶层 helper `_extract_discovery(result, seen_characters)` 纯函数好测
  - 类级 `_seen_characters: set` 跨块持久（Open Question：续跑后全量"首次"）
- **前端（3 组件 + QueuePage 集成）**：
  - `LaneView.vue`：并发车道状态机（占位/释放/失败态/超上限/重试同 id）
  - `DiscoveryFeed.vue`：ring buffer 200，hover 暂停滚动，新伏笔/新人物紫色高亮（#534AB7）
  - `RunDashboard.vue`：4 卡聚合（已完成/ETA/本会话输入输出）+ LaneView + DiscoveryFeed
  - `QueuePage.vue` 左 split-col tab 化：「运行概览」/「章节详情」；默认 running→概览，否则详情；用户手动切换本会话记忆
  - `useProgressSocket` ProgressMessage type 联合加 `'block_start' | 'discovery'`
- **测试**：后端 +5 / 前端 +12（LaneView 5 / DiscoveryFeed 5 / RunDashboard 2）；全量 293 + 47 passed / 0 回归；vue-tsc 0 错；vite build 0 错
- **分支**：全程在 `h16-recovery`（feature/streaming-tokens 名字被并发 watcher 针对）
- **不做**：流式 content_delta 输出 / 跨块伏笔去重 / 车道进度条+卡死预警（V2 升级，依赖 H16 Phase 3 业务接入）

## 2026-08-26 日志轮转彻底修复（H15）

- **`desktop.py` crash.log 轮转 bug**：`_StderrTee` 用独立 `open("a")` 句柄写 `crash.log`，与同文件 `RotatingFileHandler` 句柄并存，Windows 下 `os.rename` 因 `ERROR_SHARING_VIOLATION` 失败 → 异常被 `logging.handleError` 写回 stderr（即 crash.log）→ 形成「永不轮转 + 永不增长上限」反馈环。**实测** crash.log 60MB 无 `.1` 备份
- **`llm_client.py` FailureLogger 无大小上限**：原 `open("a")` 裸追加，24h-unlink 仅在 `__init__` 触发一次；长跑不重启文件只增不减
- **修复**：`_StderrTee` 改走 `crash_logger` 的 `RotatingFileHandler`（统一流/锁/轮转），`FailureLogger` 改用 `RotatingFileHandler(10MB × 3)`，取消 24h-unlink
- **统一策略**：所有 log 单文件 ≤10MB，备份 3 份（30MB 总占用上限）
- **回归**：255 pytest 全过，行为契约零变动；轮转备份在桌面端重启后首次到 10MB 时自动出现
- **未动**：`%LOCALAPPDATA%\NovelAnalyzer\run.log` / `build.log`（不在工程内）

---

## 2026-08-31 运行概览车道堆叠修复（车道注册表自愈）

- **症状**：长程运行后「并发车道 57 / 8 运行中」极夸张堆叠；原有功能无影响。WS 挂测试客户端三轮实测定位：真实 LLM 并发恒等于配置值（信号量从未超卖），纯前端车道记账腐烂
- **根因（三层叠加）**：
  1. 记账口径：`activeBlocks` =「start 未 done」，但 done 由 as_completed 消费循环补发（worker 已释放槽位）且每 8 块阻塞等 rolling 总结 1-3 分钟 → 稳态虚高 ~2.5 倍（实测峰值 20/8）
  2. 通道有损：ProgressHub 每连接队列 maxsize=1000 满了丢最旧 + 断线无重放 + 前端无对账 → 丢一条 done = 永久僵尸（只能刷新页面清零）
  3. 触发点：断点续跑补发洪峰（916 块 ~2700 条消息必打满队列）+ LLM 失败风暴日志（实测 21:51-21:52 两分钟 73 次尝试失败）+ rolling 等待
  - 附带排除并堵上两条隐藏泄漏路径：skipped（审核拦截）原先不转发 block_done；worker 异常兜底固定发 chapter:0 被前端 `blockId > 0` 检查忽略
- **修复**（subagent 复审通过并按其 4 条修正落地）：
  - `pipeline.py`：`_analyze_one_block` try/finally 登记 `_inflight_blocks`（预热/并发/补跑三路径单点覆盖，信号量真实在途 ≤ concurrency）+ `inflight_blocks()` 快照；`_worker`/`analyze_block_with_progress` 异常兜底带原 block_id 发 failed + 补 `state.add_failed`；续跑补发每 50 条 sleep(0)
  - `queue_service.py`：`status()` 暴露 `inflight_blocks`（getattr 兜底 `__new__` 单测实例）；`skipped` 也转发 block_done(ok=false)
  - `progress_hub.py`：队满分级驱逐——先丢 token_delta（高频可再生）→ 再丢 log → 保 block_start/block_done 等低频关键事件
  - 前端新增 `utils/laneRegistry.ts` 纯函数：`gcActiveBlocks`（超预警窗无 token / 30min 硬上限回收僵尸，跳过 ≥900000 总结合成车道 + has-token 门控防误杀）+ `reconcileActiveBlocks`（inflight 整体纠偏，保留合成车道与 15s 宽限新块，无变化返回原引用）；`QueuePage.syncLaneRegistry()` 挂 `refresh()`（5s 轮询 + block_done 防抖），GC 同步清理 tokenByBlock/rateByBlock；client.ts `AnalysisStatus` 加 `inflight_blocks` 类型
- **回归**：后端 420 passed（+6：hub 分级驱逐 3 / pipeline 在途+异常 3，54 文件）；前端 vitest 77 passed（+11 laneRegistry，11 文件）；vue-tsc 0 错；vite build 通过
- **未做**：rolling 等待与消费循环解耦（消除 done 延迟基线，行为改动大，先观察自愈效果再定）

## 2026-08-31 queue_service 职责拆分（1003 行 → 4 模块）

- **`queue_manager.py`（408 行）**：QueueItem/QueueManager（状态机/并行扫描/API 预检/持久化）独立；`check_api` 的 `LLMClient` 随迁，test_provider_heal 8 处 patch 字符串同步迁移（含 :61 整类重绑定）
- **`analysis_stats.py`（119 行）**：新类 AnalysisStats 收敛 service 的 7 个散属性（token 五件套/起止时刻），方法 reset/record_chapter/accumulate_token_stats/baseline/freeze/session_snapshot/book_consumption；**不持有 pipeline**——运行中实时 KV 命中由 service 传参
- **`pipeline_events.py`（167 行）**：`make_pipeline_callbacks(hub, item, stats, seen_characters)` 工厂替代 `_run_one_item` 的两个嵌套闭包（捕获面经复审确认恰好完整）；discovery 提取/ETA 格式化随迁
- **`queue_service.py`（406 行）**：只留 AnalysisService 编排 + get_service，符号名与文件名不动（summary_service 反向导入方向不变）；`_record_chapter_stat` 退化为测试拦截垫片
- 拆分前 subagent 对照代码复审，吸收 3 处修正（routes_analysis 生产导入不能破 / AnalysisStats 补 freeze+baseline / 每步绑定测试改造）；三步各自提交后全量 pytest 420 passed，`from backend.app import app` 导入冒烟通过
- **未动**：自动总结/归档编排（与分析-总结互斥锁纠缠，等有功能需求时再拆）

## 2026-08-31 WS 事件协议类型化（前后端，wire 格式零变更）

- **后端 `backend/ws_events.py`（新，协议唯一权威定义）**：`WSType`(11 值)/`WSState`(12 值)/`PipelineStatus`(7 值) 三个 StrEnum + 8 类 payload 契约 + 7 个纯同步工厂（log_event/progress_event/block_start_event/block_done_event/discovery_event/state_change_event/token_delta_event——HubLogHandler 同步线程可调）
- **后端接线**：pipeline.py 12 处 `_emit` status 常量化；pipeline_events.py 比较与发布改工厂（含删 `_record_chapter_stat` 之外的旧发现：progress_hub.block_done() 便捷方法全仓无调用方且 payload 形状与翻译层不一致，已删除并在 ws_events.block_done_event 注释留痕）；queue_service(6)/summary_service(4+2)/location_normalization_service(5+1)/app.py(2) 全部常量化；progress_hub.state_change 签名收紧为 WSState
- **前端**：useProgressSocket 定义 10 类 payload 接口 + 可辨识联合 ProgressMessage（ping 无 payload）；QueuePage 删 5 处 `as unknown as` 强转（switch 自动窄化，只删强转不改 `??` 兜底语义）；App.vue 删 7 处 as 强转；summaryLanes 入参类型化 `WSSummaryProgressPayload`（字段全 optional 兼容测试 fixture）
- **验证**：后端 420 passed + 导入冒烟；前端 vitest 77 passed + vue-tsc 0 错 + vite build 通过
- **收益**：状态漏发/字段拼错不再静默通过（类型层拦截），WS 协议有了唯一权威文档（ws_events.py 即协议文档）

## 2026-08-31 llm_client 拆分（1761 行 → 4 模块，主类 1291 行）

- **`llm_failure_logger.py`（104 行）**：FailureLogger 类 + 进程级单例 + `_get_failure_logger()` 原样随迁（纯 stdlib；全仓仅 llm_client 内部使用，零测试改动）
- **`llm_probe.py`（277 行）**：`probe_thinking_params` 多模式思考检测 + `_THINKING_DETECTORS` + `_get_reasoning_tokens`（全库仅 probe 使用）转模块函数；LLMClient 删除该 staticmethod。调用方同步：routes_settings probe 端点、test_anthropic_provider 9 处（函数级导入/调用/AsyncOpenAI patch 字符串）、test_streaming 1 处（复审纠正：:563 的 patch 实为 probe 用途而非流式测试）
- **`llm_stream.py`（133 行）**：StreamChunk/StreamResult 数据类 + 三个解析纯函数（`parse_openai_stream_event`/`parse_anthropic_stream_event`/`parse_anthropic_response`，方法体无 self）转正；llm_client 三个调用点、analyzer 导入、final_summary 延迟导入、test_streaming 5 处 + test_anthropic_provider 2 处方法调用同步改写
- **llm_client.py 保留**：LLMClient 类 + chat/chat_with_retry/chat_stream_with_retry/chat_auto 重试核心（与 self 状态深度集成，机械拆分有行为风险——刻意不动）；AsyncOpenAI/AsyncAnthropic 导入必须留在本文件（流式路径的 patch 依赖）
- 拆分前 subagent 对照代码复审，吸收 2 处遗漏调用点；三步各自提交后全量 pytest 420 passed，`from backend.app import app` 导入冒烟通过
- **未动**：`moderation_hit_from_exception`（18 行，迁移价值低）、`_build_anthropic_payload`（读 self.config 保留为方法）、`detect_provider`（测试直接导入，留 llm_client）

## 2026-08-31 工作区清理

- 删除已被完全取代的两份整仓备份：`小说分析器 - 副本`（332MB）+ `小说分析器 - 归一化兜底版`（358MB）。删除前验证：基线提交 cae160a 在主仓历史内、两份备份全部改动/新增文件在主仓均有对应（内容已被后续正式实现覆盖）；`代码版本/`（git 化前手动版本档案）与 `数据产物/`（分析结果归档）保留
- 项目内清理：tmp_* 草稿 ×5、`crash.log.1`（62MB，H15 修复前的旧轮转备份）、`__pycache__`/`.pytest_cache`、前端构建残留（vite.config.js/.d.ts、tsbuildinfo ×2，均 gitignored 可再生）
- 删除 git 跟踪的死文件：4 张无引用的视觉验证截图（frontend/*.png）+ `.github/workflows/ci.yml`（仓库无远端，Actions 永远不会触发；均可从历史恢复）

---

## 核心机制演进主线

| 机制 | 雏形期（3月） | PyQt6/PySide6 期（4-7月） | Web 版（8月） |
|---|---|---|---|
| 分析引擎 | 单块顺序 + knowledge.json | 串行预热 + 分片/流式并发 + 失败 3 轮补跑 | 同上 + 快照隔离（并发正确性） |
| 记忆 | timeline + 压缩弧光 | 结构化滚动总结（里程碑/范式层/因果链/势头） | 同上 + 断号容错 + KV cache 前缀优化 |
| 伏笔 | 无 | 回收机制 → 伏笔账本（06-16/06-17） | **50 类分类体系**（源头约束+映射兜底）+ 总表过滤 |
| 最终总结 | 无 | 分批卷摘要+报告（05-17）→ 4 阶段（调和/复检/风格/报告） | 4 阶段 + **完整断点续跑** + 独立模型/思考选择 |
| 容错 | 温度退火 | + 指数退避 + 7 层 JSON 容错 + FailureLogger | + 漏引号定向修复 + 重试成本透明 |
| GUI/形态 | tkinter | PyQt6 → PySide6（Fluent 主题） | **FastAPI + Vue3 + pywebview** |
| 队列 | 无 | 无（单书）→ QueueManager（07-26） | 队列服务 + 自动总结 + 断点恢复 |

## 实际战绩（可复现的里程碑结果）

- 《三侠五义》121 章：5/17 并行模式半小时全书分析 + 最终总结报告（PyQt6 版）
- 5 本书批量风格对比（6/1）：三侠五义/圣墟/大王饶命/永夜君王/超神机械师
- 《卡徒》514 章队列分析（7 月底，block_size=2 断点续跑验证）
- 《北宋穿越指南》497 章（8/9）：伏笔 50 类归一化全量映射 1663 种 type、0 幻觉、时间线 2786 条
- 《白首妖师》500 章（8/9）：新约束 prompt 首战（48 种 type 全合法），最终报告经切换 deepseek-v4-flash 一次通过
- 归档书目：87+ 本（微型集群/分析结果），覆盖玄幻/武侠/都市/科幻等题材
