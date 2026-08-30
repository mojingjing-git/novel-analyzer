# 审核报告 #2：双层模型路由

> 审核对象：改进方向 #2 —— 在 analyzer 引入"章节复杂度分级"，简单章路由到便宜模型以省 30-50% API 成本
> 审核日期：2026-08-27
> 审核范围：`backend/core/{analyzer,llm_client,pipeline}.py` + `backend/services/{queue,summary}_service.py` + `backend/config/{settings,presets}.py` + `backend/core/{memory_state,prompt_builder}.py`
> 审核者：Worker 子 agent
> 用户画像：辅助阅读 + 拆书（非创作），预算敏感（个人项目）

---

## 1. 源码调研摘要

### 1.1 LLMClient 是单例还是多实例？

**答：进程内多实例、按"分析会话"隔离。**

具体拓扑：

| 实例化点 | 文件:行 | 生命周期 | 用途 |
|---|---|---|---|
| `NovelAnalyzer.__init__` | `core/analyzer.py:60` | 一次分析运行（pipeline 级别） | **所有章节分析的唯一 LLM 入口** |
| `AnalysisPipeline.run` | `core/pipeline.py:401` | 同上 | rolling 摘要独立客户端（注释明确："不污染主分析 stats，不走重试链"） |
| `FinalSummaryRunner.__init__` | `services/final_summary.py:547` | 一次总结运行 | 最终报告，**已支持 summary_model 路由** |
| `LocationNormalizationService` | `services/location_normalization_service.py:83` | 单次 RPC | 地点归一化，**已支持 summary_model 路由** |
| `StyleAnalyzer` 工具函数 | `core/style_analyzer.py:325` | 单次调用 | 风格分析 |
| `QueueManager.check_api` | `services/queue_service.py:361` | 探针临时 | 启动期连通性 |

**对路由方案的影响**：

- 章节分析由 `NovelAnalyzer.llm_client`（一个 client）服务**所有 chapter**（pipeline 一次只 new 一个 analyzer，`pipeline.py:365`）
- 不能像 rolling/summary 那样在构造时"按用途固定模型"——模型是**逐章可变的**
- 因此**路由点必须落在 analyzer.analyze_chapter() 内部**（`analyzer.py:108` `try` 块起始），不能落到更外层

### 1.2 模型字段被消费的所有位置

经 `grep` 全文 `config.api.model` / `self.config.model` / `api_cfg.model`，运行时消费点有 7 处：

| 消费点 | 文件:行 | 消费方式 |
|---|---|---|
| `chat()` 调 OpenAI | `core/llm_client.py:654` | `model=self.config.model` |
| `chat()` 调 Anthropic | `core/llm_client.py:588` | `_build_anthropic_payload` 读 `self.config.model` |
| `chat_stream_with_retry` | `core/llm_client.py:1145` | 日志 `Model: {self.config.model}` |
| `chat_with_retry` | `core/llm_client.py:1515` | 同上 |
| `probe_thinking_params` | `core/llm_client.py:485` | `model=model` 形参（由调用方传） |
| `check_api` 空检查 | `services/queue_service.py:345` | `if not str(config.api.model or '').strip()` |
| `probe_thinking_params` API 路由 | `api/routes_settings.py:82` | `config.api.model` 形参 |

**关键观察**：所有运行时消费都从 **`self.config.model` 单一字段** 读取。即"切换模型"的最小代价面是 **LLMClient 的 `self.config` 引用**——一个 dataclass replace + 单字段重赋值即可，无须重建 OpenAI 客户端连接池。

### 1.3 summary_model 现有实现可借鉴的部分

**这是本次改进的黄金参照模板**。`APIConfig` 已有的两行字段：

```python
# backend/config/settings.py:141-142
summary_model: str = ""      # 最终总结专用模型；空=跟随 model
summary_thinking_mode: dict = field(default_factory=dict)  # 空=跟随 thinking_mode
```

已被两个调用方"按 `X or Y` 短路"使用：

```python
# services/final_summary.py:540-547
_api_cfg = replace(
    self.config.api,
    model=self.config.api.summary_model or self.config.api.model,
    json_mode="default",
    timeout=self.config.api.summary_timeout,
    thinking_mode=self.config.api.summary_thinking_mode or self.config.api.thinking_mode,
)
self._llm = LLMClient(_api_cfg)
```

**这套范式的可借鉴点**：

1. **配置兼容零成本** —— `_coerce_fields`（settings.py:61-122）已能容错旧 config.json 缺字段；`from_dict` 显式 `pop` 已废弃字段并 warn（settings.py:240-260）；新字段加在 `APIConfig` 默认值即可，老配置文件加载不会崩
2. **Provider heal 链路完整** —— `check_api` 的 provider 纠正逻辑（queue_service.py:381-399）只对 `config.api.model` 做 claude-* 前缀纠正；如主模型用 M3、次模型用 deepseek-chat，**不会误判**，因为纠正条件是"OpenAI 网关暴露 claude"——主模型以 OpenAI 协议暴露 M3 是合法形态
3. **thinkging_mode 隔离** —— `summary_thinking_mode or thinking_mode` 的短路策略可 1:1 抄到 `secondary_thinking_mode or thinking_mode`

**但有 3 个 summary_model 模式下没有的差异点需额外设计**：

1. summary_model 是"整次 final_summary 用一个固定模型"（替换式），secondary_model 是"逐章按规则挑一个模型"（路由式）
2. summary_model 不参与并发；secondary_model 必须和主模型在并发池里共存——并发上限、温度退火、stats 累积都要双轨
3. summary_model 不写回 result 落盘；secondary_model 需要在 `chapter_*_result.json` 记下"用哪个模型跑的"（否则断点续跑无法识别混合来源）

---

## 2. 多角度评分

| 维度 | 分数 | 简短理由 |
|---|---|---|
| 1. 改进难度 | **3/5** | 仿 summary_model 范式本身简单（2 字段 + 1 路由函数），但**复杂度判定规则**、**并发双 LLMClient / 单 LLMClient 选择**、**KV cache 重置**是真正难点，至少 200-300 行 + 单测 |
| 2. 改进收益 | **2/5** | 提案假设省 30-50% 成本；但**实际可能被 KV cache 失效吃光**——见 §4.3 与 §7 |
| 3. 代码复杂度提升 | **3/5** | 新增 1 模块（routing.py 或嵌入 analyzer）+ LLMClient 内部加 model 维度桶；调用链多一层；并发/重试逻辑被双模型化但不强耦合 |
| 4. 维护难度 | **3/5** | routing 规则需要可调；误判"复杂章"导致质量下降的故障模式难以回溯；AB 切换时老 result 与新 result 共存无 schema 校验 |
| 5. 兼容性风险 | **4/5** | ①断点续跑：旧块用主模型、新块按规则切次模型，质量不均无 schema 校验；②温度退火：单 LLMClient 内按 model 分桶需重做；③前端 StatsPage 没 model 维度；④check_api 只探主模型 |
| 6. 测试覆盖成本 | **4/5** | routing 规则本身好测（纯函数），但需要"模拟并发场景下双模型交互"——既要 mock OpenAI client 区分主/次响应，又要测退火/stats/cache 桶对齐；至少 5-8 个新单测 |
| 7. 实施风险 | **4/5** | ①复杂章误判为简单章 → 质量下降且**无法自动恢复**（re-analysis 也走同一规则）→ 用户感受是"拆书变浅"；②次模型失败 fallback 链未设计（简单章失败是 fallback 主模型还是走完整重试？）；③两个模型同 base_url 时无差别——见 §7 替代方案 |

**总评**：2.7 / 5。**属于"实现不难、收益可疑、风险分散"的改进**。如果用户目标是"省成本"，更稳的路径是先做"P1：缓存命中优化 + 温度退火利用率提升"（见 §7），再做 P2：双层路由。

---

## 3. 关键发现

### 3.1 LLMClient 内部 `self.config.model` 是单一真相源

`LLMClient.__init__`（llm_client.py:254-276）将整个 APIConfig dataclass 挂在 `self.config`，所有 chat/chat_with_retry/chat_stream_with_retry 路径都从 `self.config.model` 读模型名。

**这意味着：让"次模型"工作有两种实现路径**

| 路径 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A. 单 LLMClient + model 维度桶 | 给 chat 加 `model_override` 参数；LLMClient 内部按 model 分桶维护 stats/退火/failure_log | 温度退火连续性保留；stats 区分清晰；不改架构 | LLMClient 内部多一个维度的状态（修 _stats_lock / _attempts 维护） |
| B. 每次路由 new 一个 LLMClient | analyzer 内部 cache 两个 LLMClient 实例（主/次），路由时取对应实例 | 改动局部、复用 summary_model 范式 | 温度退火**完全割裂**（次模型每次重试都从 0.1 起步，不递增）；新 LLMClient 初始化 OpenAI client 有连接池开销；两实例 stats 合并需额外代码 |

**建议选 A**。但 A 需要在 LLMClient 引入"per-model state bucket"概念——是相对大的改动。

### 3.2 复杂度判定规则存在根本困难

提案规则：
> 简单章（字数<3000、角色≤3、无伏笔关键词）

- **字数** ✅ 可本地算（`len(chapter_content)` 或 `count_cjk()`，项目已有 style_analyzer.py:106）
- **角色≤3** ⚠️ 可估算但**不准**——KB 里只有 `character_states`（已登场角色），无法判断本章实际出场几个；可拿"本章 character_states 命中数"做粗略估计，误差大
- **无伏笔关键词** ❌ **不可本地算**——伏笔是 LLM 输出；KB 里只有 `rolling_structured.active_causal_chains`（结构化滚动摘要里的因果链），但这是历史累计的，不是本章的

**可妥协的实现**（零 LLM 调用）：

```python
def is_simple_chapter(content: str, kb: KnowledgeBase) -> bool:
    char_count = count_cjk(content)
    recent_arcs = len(kb.compressed_arcs[-5:])  # 最近 5 块的弧数
    causal_chains = len(kb.rolling_structured.get("active_causal_chains", []))
    return char_count < 3000 and recent_arcs <= 1 and causal_chains <= 2
```

但这只能判"近期剧情不复杂"——可能错判"本章独立小剧场但引入新人物"为简单章。

### 3.3 温度退火必须按 model 分桶

`llm_client.py:1230`：
```python
temp = max(0.0, self.config.temperature - attempt * self.config.temperature_step)
```

`self._attempts`（llm_client.py:271）是当前 LLMClient 的全局计数；`self._total_tokens` / `self._failed_tokens` / `self._cached_tokens`（272-274）都是全局。

**双模型时**：

- 如果用路径 A（单 client + override）：`_attempts` 等统计必须按 `(model, ...)` 分桶，否则主模型重试次数会被次模型稀释
- 退火是从 `self.config.temperature` 起始算术递减——**跨模型共享同一退火起始值是错的**（主模型温度 0.1 + step 0.05；次模型可能希望 0.3 + step 0.1）
- 因此需要为次模型新增 `secondary_temperature` / `secondary_temperature_step`（或更干净的：在 routing 决策里直接给每章指定完整 APIConfig 子集）

### 3.4 LLMClient 已有 KV cache 基础设施但路由会让其失效

`llm_client.py:55` `StreamChunk.usage_cached_tokens` + `llm_client.py:704/733/1290/1398/1759` 的 `self._cached_tokens` 累加——基础设施完备。

**但**：prompt cache（KV cache）依赖**前缀 token 序列完全一致**。路由切到次模型时：
- 主模型缓存的 15-20K token 上下文（system + 滚动摘要 + 伏笔网络）**完全作废**
- 次模型首次调用要付全价（input tokens 全额计费）
- OpenAI 提示缓存命中率约 60-80% → 0%

**实际成本节省估算**（粗算，假设：50% 简单章、单章 input 20K token、input $3/M、output $15/M、cache 命中率 60%、cache 命中价 1/10）：

| 模型 | input $/M | output $/M | 单章全价（input 20K + output 2K） | 50% cache 命中时实际成本 |
|---|---|---|---|---|
| 主模型（如 M3 thinking） | 3 | 15 | $0.090 | $0.090 × 50% = $0.045（含 cache 折扣后实际约 $0.020） |
| 次模型（如 GPT-4o-mini） | 0.15 | 0.60 | $0.0042 | $0.0042（无 cache，全部原价） |

实际"复杂章成本" = $0.020（含 cache），"简单章成本" = $0.0042，**简单章确实便宜 5 倍**。

但**前提是 50% 的章被路由到次模型**——网文 13 本分析里大部分章节字数偏短（《大王饶命》单章 2-4K 常见），大概率 60-70% 满足简单章条件，**单书潜在节省 35-45%**。

**但还有隐性成本**：
- 次模型 thinking 弱：可能漏判伏笔/人物 → **质量下降 → 用户回头重跑**
- 简单章走次模型时仍要构建完整 KB 上下文（9 路 context 都是本地算的，不省钱）；省的是**模型推理费**，不是 prompt 构建费

---

## 4. 实施风险

### 4.1 复杂度判定规则冲突时怎么办？

**现状**：无规则、无 fallback。**建议实施时**：

1. **默认主模型**（路由关闭时所有章走主模型，与现状 100% 一致）
2. **routing.enabled=false** 时整段逻辑跳过
3. **判定歧义**（字数 2999 vs 3001 这种临界）→ 按"宁可保守"原则，超阈值即视为复杂章
4. **判错时**：**不重路由到主模型**（避免"次模型失败 → 主模型成功 → 同一章混两模型"），但**记录到 stats 供后续人工 review**（在 `chapter_stats` 加 `routed_to: "main" | "secondary" | "fallback_main"`）
5. **路由决策本身入 result 落盘**：在 `chapter_*_result.json` 加 `model_used` 字段（与 block_size 同一 schema 升级周期），断点续跑可读到

### 4.2 secondary model 失败时 fallback 策略

**未在提案中明确**。三种可选方案：

| 方案 | 行为 | 适用 |
|---|---|---|
| F1. 主模型重试链 | 次模型失败 → 同章 fallback 主模型 + 走完整温度退火链 | 简单章少、容错优先 |
| F2. 原样失败、补跑期主模型重试 | 次模型失败 → 标 failed；3 轮补跑阶段用主模型重跑 | 简单章多、成本优先 |
| F3. 次模型完整重试链（不 fallback） | 次模型失败 → 走次模型自己温度退火；仍失败 → failed | 不推荐（次模型可能根本没 disable thinking 能力） |

**推荐 F2**。理由：
- F1 浪费 fallback 的 token（与"省成本"目标矛盾）
- F2 让补跑阶段（3 轮，pipeline.py:525-542）自然处理混合模型——已有 `_failed_chapters` 状态机，复用零成本
- 失败时已经走的调用是次模型的"低质量"输入，重跑换成主模型反而提升 KB 合并质量

### 4.3 对 prompt cache（前缀复用）的影响

**显著负面影响**。见 §3.4 详细测算。结论：

- 网文 prompt 实际 input 中 system + 滚动摘要 + 伏笔网络占比 50-60%（"九路 context"）
- 切次模型时**这些前缀全部 cache miss**
- 简单章走次模型**至少要付 50% 满价 input**（甚至更多，因为 rolling_structured 随章节增长）
- 实际节省可能从 30-50% 跌到 **15-25%**（仅"模型推理费"省，prompt cache 红利全丢）

**可选对策**：

- **接受损失**：简单章本来 prompt 不大（KB 早期阶段），损失可接受
- **次模型也用同一 prompt 模板**：必须，但显然
- **次模型用 prompt caching 友好的 API**（如 Anthropic cache_control）：但这与"次模型 = 便宜"的初衷矛盾

### 4.4 温度退火是否要为两个模型各自维护

**是，必须**。理由：

- 主模型可能默认 thinking=on，希望 temperature 0.1 → 0.0 退火
- 次模型可能默认 thinking=off，希望 temperature 0.3 → 0.0 退火
- 同一起始温度会让次模型在前 1-2 次重试就"过冷"，导致 JSON 输出格式死板

**实现代价**：

- `LLMClient` 需要 `dict[str, int]` 形式的 `_attempts_by_model`、`_total_tokens_by_model`、`_cached_tokens_by_model`
- `get_stats()`（llm_client.py:1753-1762）返回结构要升级（向后兼容：旧字段保留 = 累计值；新字段 `by_model` 给出分桶）
- `FailureLogger` 也建议按 model 分桶（api_failures.log 写入加 model 字段）
- 至少 100-150 行 + 8+ 个新单测

---

## 5. 兼容性影响

### 5.1 对断点续跑的影响（不同 model 跑过的块混合）

**有，但可控**。

- 旧 result 不记 `model_used`（见 §3.4）→ 续跑时无法识别"哪些块是用主模型跑的、哪些用次模型"
- 现有 `memory_state._validate_block_size`（memory_state.py:451-466）只校验 block_size，**不校验 model**
- 后果：同一本书可能 ch1-50 用主模型跑、ch51+ 改配置切次模型跑，**续跑不会重做 ch1-50**，KB 合并时伏笔质量不均
- **解决**（必须做）：在 `_validate_block_size` 同时校验 `model_used` 字段；不匹配 → 跳过（让用户重跑或接受混合质量）

**schema 升级路径**（建议）：
- `_DEFAULT_MODEL_USED = "main"` —— 旧 result 视为"主模型"
- 续跑时如 `expected_model != stored_model` → warn + 跳过（与 block_size 不匹配同策略）
- 提供显式 `--force-reroute` 入口

### 5.2 对前端 StatsPage / SessionTokenStats 的影响

**影响中等，需要前端配合**。

- `chapter_stats` 字段（queue_service.py:556-564）当前只记 `(chapter, status, elapsed, input_tokens, output_tokens, retries, failed_tokens)`——**没有 model 字段**
- `_record_chapter_stat`（queue_service.py:554-566）需要补 `model_used` 参数
- `_emit_tokens` 回调（queue_service.py:817-826）的 categories 字典可加 `chapter_main` / `chapter_secondary` 两个 key
- 前端 `StatsPage` 需新增 model 维度展示（饼图/分栏）

**简化路径**：如果用户（你）能接受"前端看不出哪些章用了次模型"，可以**只写后端 schema、不改前端**——但这违背"先做用户可感知的改进"原则。

### 5.3 对 config 兼容性的影响（新增字段老 config.json 能否加载）

**完全无影响**。

- `APIConfig` 新增 `secondary_model: str = ""`（仿 `summary_model`）→ `_coerce_fields` 自动按默认构造
- 新增 `secondary_thinking_mode: dict = field(default_factory=dict)` → 同上
- `AppConfig.from_dict` 不需要改
- `ConfigManager.load()` 不需要改
- 旧 config.json 缺这两个字段 → 自动回落默认（空 = 跟随主模型 = 路由关闭）

---

## 6. 建议优先级

**P3**（建议低优先级、可选实施）

理由：
1. 改进难度中等但收益不明确（KV cache 失效吃光节省）
2. 实施风险高（误判质量下降 + 断点续跑混合来源 + 前端要改）
3. 用户画像是"个人项目、辅助阅读"，**质量优先于成本**——拆书变浅比多花 50% 钱更难受
4. 有更稳的省钱路径（P1：缓存命中优化、prompt 截断优化、temperature 退火利用率）

**如果仍要实施，建议降级为 P1 简化版**（见 §7）。

---

## 7. 替代方案或简化版

### 7.1 替代 A：只对总结阶段做双层（已在用）

- 当前已有 `summary_model`（final_summary.py / location_normalization_service.py 都在用）
- 覆盖了**重型任务**（卷摘要、最终报告、伏笔复检、风格提取）的成本
- 章节分析**不在范围内**——成本压力本来就不大（每章 prompt 几 K token、单章 1-2 元）

### 7.2 替代 B：只对热度低 / 续跑 / 补跑阶段做

- 预热 + 首次分析用主模型（保证质量）
- **补跑阶段（3 轮，pipeline.py:525）**用次模型（这些章已经失败，重跑也未必好，试试便宜模型）
- 收益有限，但**零断点续跑风险**（都是重跑，无历史结果混合）

### 7.3 替代 C：模型差异但同 base_url

- 如果你的主模型是 M3、次模型想用 M2-lite，**两者都走 MiniMax API**
- 好处：check_api 一次探针覆盖；同 provider 行为对齐（Anthropic 协议差异问题不存在）
- 简化 LLMClient 改造：`_build_anthropic_payload` / `chat.completions.create` 路径不变，只换 `model` 字段

**如果坚持做双层路由，强烈建议 C + 简化版的组合**：
- 限制 secondary_model 必须与主模型同 provider（前端校验）
- 仅对字数<2000 + character_states 命中数≤2 的章路由（最保守的规则）
- 不增加 secondary_thinking_mode（用主模型的，简化状态）
- 不增加 per-model stats 桶（接受 stats 混合，**但 stats 改为分别用 key `chapter` / `chapter_secondary` 区分**——成本最低）

### 7.4 替代 D：先做"低垂果实"——优化现有成本

| 优化项 | 预期节省 | 实施成本 |
|---|---|---|
| Prompt 截断优化（max_arcs 5→3、max_summaries 8→5） | 15-20% input | 极低（仅改默认值 + 单测） |
| 启用 Anthropic cache_control（Anthropic provider 时） | 30-50% Anthropic input | 低（需 LLMClient 加 1 个分支） |
| 伏笔复检批大小从 30→50（已经做了，foreshadow_recheck_batch_size） | 已生效 | 0 |
| 滚动摘要 prompt 精简（_extract_chapter_number 等可去除） | 5-10% rolling input | 中（需修改 _update_rolling_summary） |
| 禁用不必要的 thinking（_THINKING_DETECTORS 探测后用 disable 参数） | 20-30% 思考链 token | 已在做（probe_thinking_params） |

**建议**：先做 D（总节省 30-50% 但风险接近 0），再做 C（如果还有预算压力）。

---

## 8. 实施路径

如果确定按 P3 完整实施双层路由，建议分 3 阶段，每阶段独立可回滚：

### Phase 1：基础设施（无用户可见改动，约 1-2 天）

1. `APIConfig` 新增 `secondary_model: str = ""` + `secondary_thinking_mode: dict`（仿 summary_model）
2. `LLMClient` 的 `chat` / `chat_stream_with_retry` / `chat_auto` 接受可选 `model_override: str = None` 参数
3. 实现：未传 override → 用 `self.config.model`；传了 → 本次调用临时用，但 `self.config.model` 不变（避免污染后续调用）
4. `LLMClient.get_stats()` 升级：保留旧字段，新增 `by_model: Dict[str, dict]`
5. 单测：mock OpenAI chat.completions.create，验证 `model=` 参数正确传递

### Phase 2：路由逻辑（纯本地、零 LLM，约 1 天）

1. 新增 `backend/core/chapter_routing.py`：
   - `assess_chapter_complexity(content, kb) -> Literal["simple", "complex"]`
   - 规则保守优先：字数<2000 + character_states 命中≤2 + active_causal_chains≤1 → "simple"
2. `NovelAnalyzer.analyze_chapter` 在 `analyzer.py:110` `build_messages` 之后插入 `model_for_this_chapter = self.config.api.secondary_model if assess == "simple" else self.config.api.model`
3. `LLMClient.chat_auto` 调用传 `model_override=model_for_this_chapter`
4. 单测：boundary 条件（2999/3001 字）、并发场景（同时 5 简单 + 3 复杂章）

### Phase 3：落盘 + 续跑 + 前端（约 2 天）

1. `AnalysisResult` 加 `model_used: str = "main"` 字段（默认值兼容旧 result）
2. `_handle_block_outcome` 写入 `result.model_used`
3. `memory_state._validate_block_size` 增加 `model_used` 校验（仿照 block_size 校验逻辑）
4. `_chapter_stats` 加 `model_used` 字段；`_emit_tokens` 的 `categories` 用 `chapter_main` / `chapter_secondary` 分桶
5. 前端 StatsPage 展示 model 维度（建议：先打日志，确认分布健康后再加 UI）
6. `chapter_model_used.json`（类似 `token_stats.json`）落盘每本书的 model 分布

### Phase 4：fallback + 监控（约 1 天）

1. 失败 fallback 策略按 §4.2 的 F2 实现
2. `api_failures.log` 加 `model` 字段
3. `check_api` 扩展：同时探 secondary_model 可用性
4. 文档：用户手册"什么时候用双层 / 什么时候用单层"决策树

**总计**：约 5-7 天（含测试与文档），代码量约 400-600 行 + 10-15 个单测。

**前置条件**：用户（你）确认以下三件事
1. 真的省那 30-50% 重要吗？目前跑 13 本的总成本是多少？预算阈值在哪里？
2. 质量下降（伏笔漏判、人物关系简化）的可接受阈值？
3. 愿意接受断点续跑时的"混合模型 result"现实，还是希望严格隔离（如"续跑前先做 schema check，重跑不匹配的块"）？

**如果上面任一答不出来 / 答案模糊，建议先做 §7.4 替代 D（低垂果实），观察成本数据后再决策。**
