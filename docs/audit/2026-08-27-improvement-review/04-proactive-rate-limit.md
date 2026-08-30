# 审核报告 #4：主动限流

> **结论先行：不建议按当前方案完整实施**。收益边际（仅减少偶发 429 重试延迟），但需要跨 OpenAI/Anthropic 双协议改 `chat()` 成功路径、引入动态 Semaphore 替换 4 处现有 Sem、并对 9 个预设端点做 header 兼容性适配——单点失败容易把可用系统改坏。
>
> **建议拆为"档 A（仅 OpenAI/Anthropic 主动）"和"档 B（滑动窗口软限流覆盖国产）"两档，先做档 A + 探测端点**。详见 §6。
>
> 关键约束：流式调用（`streaming_enabled=True`，H16 2026-08-26 默认开启）下，OpenAI/Anthropic SDK 的 `with_streaming_response` 路径会大幅改 `chat_stream` 的生命周期（async with + context manager）—— 收益与风险比最差的恰好是默认路径。

---

## 1. 源码调研摘要

### 1.1 现有 429 / Retry-After 处理路径（精确行号）

**5 处只读 Retry-After header，全部在错误分支上做被动退避**：

| 位置 | 协议 | 行号 | 行为 |
|---|---|---|---|
| 错误 → 注入 error_msg | OpenAI | `llm_client.py:814-824` | `e.response.headers.get('retry-after')` → 拼成 ` [Retry-After:N]` 文本 |
| 错误 → 注入 error_msg | Anthropic | `llm_client.py:874-880` | 同上 |
| 退避决策 | 流式 | `llm_client.py:1273-1274, 1357-1358` | `self._retry_after_seconds(err)` 正则解析 |
| 退避决策 | 非流式 | `llm_client.py:1627-1629, 1659-1660` | 同上 |
| 工具方法 | — | `llm_client.py:1479-1492` | `_is_429` 字符串匹配 + `_retry_after_seconds` 正则解析（**只解析 chat() 已注入的 `[Retry-After:N]` 文本**） |

**没有成功路径读取 response headers 的代码**。grep 全仓只命中 2 处（`llm_client.py:817-819, 875-877`），且都来自 `e.response` 异常对象自带字段。

### 1.2 OpenAI / Anthropic SDK 拿 header 的具体方法

**OpenAI SDK（`openai>=1.x` AsyncOpenAI）**：
- **错误路径**：`e.response.headers`（`httpx.Headers`）— 当前代码已用 ✅
- **成功路径**：标准 `await client.chat.completions.create(...)` 返回 `ChatCompletion` 对象**不带 headers**。要拿 header 必须切到 `with_raw_response`：
  ```python
  resp = await client.chat.completions.with_raw_response.create(...)
  chat_completion = resp.parse()  # httpx.Response -> ChatCompletion
  headers = resp.headers  # httpx.Headers，x-ratelimit-*-tokens / x-ratelimit-*-requests / retry-after
  ```
- **流式**：`client.chat.completions.create(stream=True)` 的每个 chunk（`ChatCompletionChunk`）**无 headers**。要拿 header 必须用 `client.chat.completions.with_streaming_response.create(...)`（context manager，结构与 chat() 现有三分竞争 asyncio.wait 模式不兼容）。

**Anthropic SDK（`anthropic>=0.39` AsyncAnthropic）**：
- **错误路径**：同样是 `e.response.headers`。
- **成功路径**：`await client.messages.create(...)` 返回 `Message` 对象**不带 headers**。需要 `client.messages.with_raw_response.create(...)`。
- **流式**：`client.messages.stream(...)` 的 event 同样无 headers。需要 `client.messages.with_streaming_response.stream(...)`。

**核心矛盾**：当前默认 `streaming_enabled=True`（H16 Phase 3 2026-08-26 改造），即**绝大多数生产调用是流式**。要在流式下也读 header，必须重写 `chat_stream` 的整个异步结构——这会冲击现有的 30s 硬超时、stop_event 三分竞争、chat_stream_with_retry 重试链。

### 1.3 现有 Semaphore 拓扑

| 位置 | 用途 | 生命周期 | 数量 |
|---|---|---|---|
| `pipeline.py:441` | 分析阶段（preanalysis + concurrent + rolling） | 每次 `pipeline.run()` 重建 | `concurrency`（用户配置，默认 4） |
| `final_summary.py:525` | 总结阶段 4 阶段 + 风格共用 `_llm_sem` | 每次 `FinalSummaryRunner.__init__` 创建 | `concurrency`（用户配置） |
| `final_summary.py:1442` | 伏笔复检局部 sem | 一次 `_run_global_foreshadow_recheck` 内 | `min(concurrency, len(batches))` |
| `location_normalizer.py:553, 613` | Phase 0a/0b 局部 sem | 单次调用内 | `self.concurrency` |

**关键发现**：
- 4 个 Semaphore 都是**局部实例**，**没有跨阶段共享**。
- `AnalysisService` (`queue_service.py:468-503`) 是单例（`_service` 模块级变量 + `get_service()`），**但其持有的 LLMClient 是每本书新建一次**（`self._pipeline = AnalysisPipeline(...)`，`analyzer.llm_client` 是 pipeline 的局部实例）。
- `final_summary.py:525` 注释明确说"阶段 1/2/3/4 全部共用，硬上限 = self.concurrency"——**但**这**只覆盖 FinalSummaryRunner 内部**，不覆盖 pipeline 阶段的分析调用。也就是说，分析阶段（pipeline._llm_sem）和总结阶段（final_summary._llm_sem）**是独立两个 Semaphore**。
- **rolling client** (`pipeline.py:401`) 走的是分析阶段 sem（`_analyze_one_block` 内部有 acquire）。
- `test_global_llm_sem.py:154` 强约束 `assert sem._value == 4`，**动态 Sem 改动需要同步改这个测试**。

### 1.4 探测能力

`probe_thinking_params` (`llm_client.py:351-563`) 是已有的"探测 → 并发发请求 → 根据响应判定"模式：
- 9 个候选参数并发发请求（`asyncio.gather`），每条 30s 超时。
- 7 个多模式检测器（`_THINKING_DETECTORS`），按需返回 breakdown。
- **但只读响应 body，不读 response headers**。

可借鉴点：可以新增 `probe_rate_limit_headers(base_url, api_key, model, provider)`，发 1 个最小请求（`max_tokens=1`），读 response headers，把"哪些 header 被返回"作为元信息落盘到 `ConfigManager` 配置文件里（`api.rate_limit_probe` 字段）。**这是档 A 实施的第一步**。

### 1.5 9 个厂商预设（`presets.py:6-56`）

| 预设 | 协议 | header 兼容性（社区资料） |
|---|---|---|
| LM Studio | OpenAI 兼容（localhost） | **无**（本地推理，无服务端限流） |
| OpenCodeGo | OpenAI 兼容 | 未知（依赖上游） |
| OpenRouter | OpenAI 兼容 | **通常透传上游**，不保证都有 |
| MiniMax (minimaxi.com) | OpenAI 兼容 | **部分支持** X-RateLimit-Remaining（中文社区资料，官方未承诺） |
| 阿里百炼 DashScope | OpenAI 兼容 | **不保证**（OpenAI 兼容层未承诺） |
| 智谱清言 GLM | OpenAI 兼容 | **自定义命名**，文档不一致 |
| 火山引擎 | OpenAI 兼容 | **未知** |
| 硅基流动 SiliconFlow | OpenAI 兼容 | **依赖上游** |
| DeepSeek | OpenAI 兼容 | **不支持**（仅并发数限制） |

**结论**：9 个预设里**只有 OpenAI 官方 + Anthropic 官方**能保证完整 header 支持。其余 7 个至少有 1 个端点（DeepSeek）明确不支持，2-3 个（GLM/火山/MiniMax）行为不稳定。

---

## 2. 多角度评分

| 维度 | 评分（1-5，越低越容易） | 说明 |
|---|---|---|
| 改进难度 | 4 / 5 | 双协议 × 成功/错误/流式 3 路径 × 9 个端点适配 |
| 改进收益 | 1.5 / 5 | 仅优化偶发 429 重试延迟；当前 5 处被动退避已能恢复 |
| 代码复杂度提升 | 4 / 5 | 引入 4 个新抽象（header 解析器 / 限流状态 / 动态 Sem 包装 / 共享状态存储） |
| 维护难度 | 4 / 5 | 端点行为变化难以及时发现；OpenAI/Anthropic SDK 升级可能破内部接口 |
| 兼容性风险 | 4 / 5 | 7/9 端点无完整 header；动态 Sem 与 _llm_sem 现有契约冲突 |
| 测试覆盖成本 | 4 / 5 | 至少需 9 个 mock 场景 + 动态 Sem race condition + 流式 header 路径 |
| 实施风险 | 4.5 / 5 | 流式 `with_streaming_response` 重构会冲击 H16 现有三分竞争/重试链；动态 Sem 收缩到 0 是边缘陷阱 |
| **总评** | **强不推荐完整实施；推荐档 A 试点** |

---

## 3. 关键发现

### 3.1 不同端点 header 兼容性矩阵

按"`x-ratelimit-*-remaining` + `retry-after` 同时可得"为可用标准：

| 端点 | `retry-after` | `x-ratelimit-*-tokens` | `x-ratelimit-*-requests` | 备注 |
|---|---|---|---|---|
| OpenAI 官方 | ✅ | ✅ | ✅ | 完整支持 |
| Anthropic 官方 | ✅ | ✅ (`anthropic-ratelimit-*`) | ✅ | 完整支持，**字段名前缀不同** |
| OpenRouter | 部分 | 部分 | 部分 | 透传上游 |
| MiniMax (minimaxi.com) | ❌/部分 | ❌/部分 | ❌/部分 | 官方未承诺 |
| 阿里百炼 | ❌ | ❌ | ❌ | 社区实测无 |
| 智谱 GLM | ❌ | ❌ | ❌ | 文档未提 |
| 火山引擎 | ❌ | ❌ | ❌ | 未承诺 |
| 硅基流动 | ❌ | ❌ | ❌ | 依赖上游 |
| DeepSeek | ❌ | ❌ | ❌ | 官方仅承诺 RPM/TPM 配额 |
| LM Studio | N/A | N/A | N/A | 本地无 |

**关键观察**：用户的 13 本书实际场景中（基于 user_profile：MiniMax/DeepSeek/Qwen/GLM/Anthropic），**最多 2 家支持完整 header**。把整套 9 家都接上主动限流是空转。

### 3.2 OpenAI / Anthropic SDK 拿 header 的具体方法（再确认）

| 形态 | 标准调用 | header 可见性 |
|---|---|---|
| OpenAI 错误 | `e.response.headers` | ✅ httpx.Headers |
| OpenAI 非流成功 | `client.chat.completions.create()` | ❌ |
| OpenAI 非流成功（要 header） | `client.chat.completions.with_raw_response.create()` | ✅ `.headers` |
| OpenAI 流成功 | `client.chat.completions.create(stream=True)` | ❌（chunk 无 header） |
| OpenAI 流成功（要 header） | `client.chat.completions.with_streaming_response.create()` | ✅ context manager |
| Anthropic 错误 | `e.response.headers` | ✅ httpx.Headers |
| Anthropic 非流成功 | `client.messages.create()` | ❌ |
| Anthropic 非流成功（要 header） | `client.messages.with_raw_response.create()` | ✅ `.headers` |
| Anthropic 流成功 | `client.messages.stream()` | ❌ |
| Anthropic 流成功（要 header） | `client.messages.with_streaming_response.stream()` | ✅ context manager |

**关键约束**：**默认开启的流式路径**在两种 SDK 上**都需要重写 chat_stream 整体异步结构**才能拿 header。改 chat_stream 必然冲击 H16 现有 chat_stream_with_retry 行为契约。

### 3.3 全局 LLM Sem 和分析流水线 Sem 的协调

**当前状态**：
- pipeline.py:441 sem（分析）：生命周期 = 一次 `run()`，**只覆盖分析阶段**。
- final_summary.py:525 `_llm_sem`（总结）：生命周期 = 一次 `FinalSummaryRunner`，**只覆盖 4 阶段总结 + 风格提取**。
- location_normalizer.py:553/613 sem（地点归一）：独立。

**要"动态 Sem"必须先解决 3 个协调问题**：
1. **跨 Sem 状态共享**：分析阶段的限流状态和总结阶段如何共享？需要把"剩余配额"提升到比 Sem 更高的层级（如 `AnalysisService` 单例上的 `RateLimitState` 字段）。
2. **跨书共享**：当前 LLMClient 是每本书重建（`self._pipeline = AnalysisPipeline(...)`），如果 `RateLimitState` 在 AnalysisService 上，是天然跨书共享的；但 AnalysisPipeline/FinalSummaryRunner 内部的局部 Sem 看不到这个状态。
3. **跨进程丢失**：FastAPI uvicorn 单进程下没问题；如果将来上多 worker（如 gunicorn -w 2），状态散在 2 份内存，限流是局部的。

---

## 4. 实施风险

### 4.1 动态 Semaphore 收缩到 0 的边界

`asyncio.Semaphore` 内部计数器是 `_value`：
- `acquire()`：阻塞直到 `_value > 0`。
- `release()`：`_value += 1`。
- **没有官方方法收缩 `_value`**。
- Python 3.10+ 才有 `BoundedSemaphore`，但只是 release 时校验 `_value < _initial_value`，**也不支持动态调窗**。

**实战方案**（按推荐度）：
1. **直接 `sem._value = N`**（依赖实现细节，CPython 一直稳定，PyPy 不一定；H17 之前的 `_value` 写法见 test_global_llm_sem.py:154 测试断言）。
2. **用 `asyncio.Condition` 自己写 acquire/release**——破坏现有 4 处 sem 契约。
3. **新建一个 `AdaptiveSemaphore` 包装类**，对外提供 `acquire()/release()/set_limit(N)`。需要把所有 4 处 `asyncio.Semaphore` 替换成 `AdaptiveSemaphore`。

**风险**：方案 1 最简单但依赖未公开字段；方案 3 最干净但需要重写 4 处调用点 + 改 test_global_llm_sem.py。

### 4.2 不同端点 header 名称差异

| 协议 | 限流 header 前缀 |
|---|---|
| OpenAI 官方 | `x-ratelimit-limit-requests` / `x-ratelimit-remaining-requests` / `x-ratelimit-reset-requests`（token 维度类似） |
| Anthropic | `anthropic-ratelimit-requests-limit` / `anthropic-ratelimit-requests-remaining` / `anthropic-ratelimit-tokens-remaining` / `retry-after` |

**Anthropic 字段更详细**（含 `anthropic-ratelimit-requests-reset` / `anthropic-ratelimit-tokens-reset`）。但**前缀不同**——抽象成"限流状态"时必须双协议映射。

### 4.3 MiniMax 端点可能没有 x-ratelimit-* 头（需要先探测）

**这就是为什么建议先做"探测端点"**。在切到 `with_raw_response` 改造 chat() 之前，必须先发 1 个最小请求确认 header 形态。如果端点**完全不返回限流 header**，主动限流对该端点是空跑——直接跳过该端点。

### 4.4 跨进程/重启后状态丢失

`RateLimitState` 设计成 `AnalysisService` 单例字段是自然的（跨书共享），但：
- **重启即丢**：进程退出 → 内存状态清零 → 启动后头 1 分钟可能过载。
- **FastAPI 单进程下**没问题；**uwsgi/gunicorn 多 worker** 下状态散在 N 份。
- **降级方案**：状态在 `ConfigManager` 配置文件里持久化（`api.rate_limit_state`），代价是每次写盘 IO。

### 4.5 流式调用占多数 + header 不可见的根本矛盾

**关键事实**：`streaming_enabled=True` 是 H16 (2026-08-26) 默认值，意思是**所有生产调用走 chat_stream_with_retry**。要在流式下也读 header，必须改 `chat_stream` 用 `with_streaming_response`——会冲击：
- 三分竞争 (`llm_client.py:666-678` 的 `asyncio.wait(api_task, stop_wait, hard_timeout)`)
- `chat_stream` 内部 `async for event in stream` 循环
- `chat_stream_with_retry` 整个重试链

**非流式调用**（`streaming_enabled=False` 旧用户）走 `chat_with_retry` → `chat()`。改 `chat()` 用 `with_raw_response` 相对简单，但收益**只覆盖少数派用户**。

---

## 5. 兼容性影响

| 现有机制 | 影响 |
|---|---|
| 现有 5 处 429 退避链 | **不退化**——主动限流是叠加层，失败兜底仍是 Retry-After 解析 |
| 断点续跑 | **无影响**——主动限流不影响 MemoryState 落盘 |
| 失败补跑 | **无影响**——补跑走同样 chat() 路径 |
| 温度退火 | **无影响**——温度退火在 chat() 成功后才发生，与 header 读取是同一 success path |
| 流式前端进度 | **需谨慎**——如果改 chat_stream 走 with_streaming_response，每 chunk 触达时机可能变（context manager 边读边 parse vs 直接 async for） |
| `_llm_sem._value` 测试断言 | **需要改 test_global_llm_sem.py:154**（如果换 AdaptiveSemaphore） |
| 401/403/502/503 错误 | **无影响**——主动限流只解决 429 |
| Moderation 短路 | **无影响**——与 429 正交 |

---

## 6. 建议优先级

> **强烈建议拆为两档**，先做档 A + 探测端点。

### 档 A：仅 OpenAI/Anthropic 主动限流（推荐先做）

**范围**：
- 仅对 `provider == "anthropic"` 走 `with_raw_response.create()` 拿 header。
- OpenAI 协议暂不动（覆盖 5/9 厂商，但用户场景里 OpenAI 官方端点少）。
- **只对非流式调用**做（用户可手动开 `streaming_enabled=False` 验证）。

**不涉及**：
- 不改 chat_stream（避开流式 context manager 重构）。
- 不改国产厂商路径。
- 不做动态 Sem 收缩（先做"读取 + 日志"档）。

**交付物**：
- `probe_rate_limit_headers()` 探测函数（参考 probe_thinking_params 模式）。
- `ConfigManager` 新增 `api.rate_limit_probe = {provider: {header: value}}` 字段。
- chat() 非流成功路径上 2 行 header 解析代码（存到 `self._last_rate_limit_state`，不调 Sem）。
- 启动时自动跑一次探测；前端 UI 显示"端点限流状态"。

**工作量**：1-2 天。
**风险**：低。
**收益**：可观察、可审计，不改变行为。

### 档 B：滑动窗口软限流（覆盖国产厂商 + 端点无 header 时）

**范围**：
- 客户端本地维护"过去 60s 发送的请求数 / token 数"滑动窗口。
- 对所有端点生效（不依赖 header）。
- 当窗口内请求数达到用户配置的"安全阈值"时，主动 sleep 几秒。

**优点**：
- 不依赖任何端点 header。
- 对 DeepSeek/智谱/火山等无 header 端点**特别有效**——纯客户端行为。
- 实施简单：3 行代码可起步（队列 + 时间窗口）。

**缺点**：
- 不知道服务端真实配额，只能用"用户配置的保守阈值"。
- 配置错误反而降低吞吐量。

**工作量**：2-3 天。
**风险**：中（保守阈值需要经验值）。
**收益**：**对用户的 13 本书实际场景价值最大**——因为大部分用 DeepSeek/MiniMax/GLM。

### 档 C（不推荐）：完整动态 Sem + 主动限流

**范围**：
- 4 处 Sem 全部换成 AdaptiveSemaphore。
- 跨 AnalysisService 单例共享 RateLimitState。
- 流式 + 非流式 + OpenAI + Anthropic 全部走 with_raw_response / with_streaming_response。
- 9 个预设端点 header 兼容性矩阵全适配。

**工作量**：2-3 周。
**风险**：高（冲击 H16 现有架构 + 9 端点适配）。
**收益**：边际小（用户痛点不在 429）。

---

## 7. 替代方案或简化版

| 方案 | 投入 | 收益 | 推荐度 |
|---|---|---|---|
| **仅加探测端点**（不调任何行为） | 0.5 天 | 可观察；为后续优化铺路 | ⭐⭐⭐⭐ |
| **仅采集 header 不做限流**（写日志） | 0.5 天 | 出问题时快速定位；判断"端点是否真的返回 429" | ⭐⭐⭐ |
| **客户端滑动窗口（无需 header）** | 2-3 天 | 覆盖国产厂商；对 DeepSeek 等无 header 端点有效 | ⭐⭐⭐⭐ |
| **完整 OpenAI/Anthropic 主动限流（非流式）** | 3-5 天 | 解决 OpenAI 官方/Anthropic 官方用户的 429 痛点 | ⭐⭐ |
| **完整动态 Sem + 双协议 + 流式** | 2-3 周 | 边际收益，破坏面大 | ⭐ |

---

## 8. 实施路径（推荐）

### 第一步：探测端点（0.5 天）

- 新增 `LLMClient.probe_rate_limit_headers(base_url, api_key, model, provider)`，发 1 个 `max_tokens=1` 的最小请求。
- 读 response headers，列出"该端点返回了哪些 x-ratelimit-* / anthropic-ratelimit-* / retry-after"。
- 落盘到 `ConfigManager.api.rate_limit_probe[provider] = {header: value, ts: int}`。
- 启动时自动跑一次（异步，不阻塞）。
- 暴露到前端 UI 设置页（"端点限流状态"只读面板）。

**前置依赖**：需要先支持 `with_raw_response`，但只在探测函数里用，不动 chat()。

### 第二步：header 采集（1 天）

- 改 `chat()` 非流成功路径（仅 `provider == "anthropic"`）用 `with_raw_response`。
- 解析后的 `headers` 存到 `self._last_rate_limit_state: dict`。
- `get_stats()` 暴露这个字段（前端 UI 可见）。
- 不动 `chat_stream`、不动 Sem。

### 第三步：本地限流器（2-3 天）

- 实现 `SlidingWindowLimiter(window_seconds, max_requests, max_tokens)`。
- 在 `chat()` 进入 API 调用前 await `limiter.acquire()`。
- `chat_stream` 同样在进入前 await。
- 配置走 `ConfigManager.api.local_rate_limit = {window: 60, max_requests: 60, max_tokens: 100000}`。
- 默认值对 9 家厂商分别给保守配置（DeepSeek 60s/60req，M3 60s/30req，GLM 60s/30req）。
- **这一档**对 DeepSeek/智谱等无 header 端点**特别有效**。

### 第四步：动态 Sem（**仅在档 A 验证后再决定**）

- 实现 `AdaptiveSemaphore`，对 `asyncio.Semaphore` 做包装。
- 加 `set_limit(new_value)` 方法，**通过直接改 `._value` 实现**（CPython 一直稳定，注释清楚依赖）。
- 把 4 处 `asyncio.Semaphore` 调用点换成 `AdaptiveSemaphore`。
- 改 `test_global_llm_sem.py:154` 的 `_value == 4` 断言为新形态。
- 跨 AnalysisService 单例共享 `RateLimitState`（用 header 解析结果驱动 set_limit）。

**第四步只在前三步都跑通后再启动**。如果第三步滑动窗口已经解决 95% 场景，第四步可以推迟。

---

## 9. 关键事实清单（防错备忘）

1. **当前 5 处被动 Retry-After 退避已经能恢复 429，不丢块**。主动限流只优化"恢复速度"，不解决"是否触发"。
2. **流式是默认路径**（`streaming_enabled=True`，H16 2026-08-26 改造）。改流式拿 header 必须重构 `chat_stream`。
3. **4 处 Semaphore 是局部实例，没有跨阶段共享**。全局化需要 AnalysisService 单例承载。
4. **`asyncio.Semaphore` 不可收缩**。`._value` 字段依赖未公开实现（CPython 稳定，PyPy 不一定）。
5. **DeepSeek 官方明确不支持 header**（社区资料）。其余国产厂商行为不稳定。
6. **`test_global_llm_sem.py:154` 强约束 `_value == 4`**，任何动态 Sem 改动必须同步改测试。
7. **Anthropic header 前缀是 `anthropic-ratelimit-*`**，与 OpenAI 的 `x-ratelimit-*` 不同。抽象层必须双协议。
8. **rolling client (pipeline.py:401) 是独立 LLMClient 实例**，走分析阶段 sem，不走 final_summary 的 `_llm_sem`。
9. **每本书一个 LLMClient**（pipeline 内 `self._analyzer = ChapterAnalyzer(config.api, ...)`），重启即重置 stats。RateLimitState 必须在更高层（AnalysisService 单例）共享。
10. **probe_thinking_params (llm_client.py:351-563) 是现成的"探测模式"模板**——9 个候选并发 + 30s 超时 + breakdown 返回。新增 `probe_rate_limit_headers` 可照搬。
