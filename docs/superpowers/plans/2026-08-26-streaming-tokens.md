# 流式 LLM 输出 + 实时 token 动画（H16 / S1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **状态**：草案（2026-08-26），等用户最终批准后执行。

## Goal

把分析 + 最终总结的 LLM 调用改为**流式（stream=True）**，中间链路超时不再被静默切；同时前端 token 统计从"5s 轮询 + 假装累加"升级为**真实流式增量 + 平滑动画 + 速率曲线**，让用户对"模型是不是在跑 / 跑多快 / 还多久"有直观感知。

**不做**（明确范围外）：
- 不动 prompt 模板
- 不动 FailureLogger 轮转策略（H15 已修）
- 不动 LLM 协议探测（`_detect_thinking_in_response`）
- 不动断点续跑 checkpoint（总结 checkpoint 已落盘逻辑保留）
- 不动分析阶段的 KB 快照注入

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ 前端                                                            │
│  ┌──────────────┐  WebSocket  ┌─────────────────────┐          │
│  │ 队列/总结页  │ ◀───────▶  │ ProgressHub 后端    │          │
│  │ LiveToken    │  token_    │ (节流 5-10/s)       │          │
│  │ Counter.vue  │  delta     │                     │          │
│  └──────────────┘             └──────────┬──────────┘          │
│                                          │ broadcast            │
│  ┌──────────────┐                         ▼                     │
│  │ useProgress  │              ┌────────────────────┐           │
│  │ Socket 单例  │ ◀─── WS ─── │ LLMClient.         │           │
│  └──────────────┘              │ chat_stream_with_  │           │
│                                │ retry (AsyncIter)  │           │
│                                └─────────┬──────────┘           │
│                                          │ OpenAI/Anthropic SDK │
│                                          │ stream=True          │
│                                          ▼                     │
│                                  ┌────────────────────┐         │
│                                  │ MiniMax M2.7 / M3  │         │
│                                  │ (chunked responses)│         │
│                                  └────────────────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

**核心设计**：
1. **后端**：新增 `LLMClient.chat_stream_with_retry(messages, **opts) -> AsyncIterator[StreamChunk | StreamResult]`；保留 `chat_with_retry` 旧非流式 API 兼容现有测试和小众场景（prompt 预览、模型探测）。
2. **进度中枢**：`ProgressHub` 加 `token_delta` 消息类型；服务端**按 channel 节流**（5-10/s）避免 WebSocket 拥塞。
3. **断点续跑兜底**：总结 checkpoint（`volume_N.md` / `recon_N.json`）保持原样；流式中断时**已累积的 partial JSON** 用 9 级容错链解析，解析成功则落 `recon_partial.json`，下次启动检测到则增量补全。
4. **前端**：`useProgressSocket` 加 `token_delta` 订阅；新组件 `LiveTokenCounter.vue` 显示速率 / 已生成 / ETA / sparkline；已有 `CountUp.vue` 复用为"target value 模式"做插值动画。

## Tech Stack

- **后端**：Python 3.11+ / FastAPI / OpenAI SDK ≥1.0 / Anthropic SDK ≥0.28 / `asyncio` 流式迭代
- **前端**：Vue 3.5 + TS 5.7 + Composition API / `@vueuse/core` 用于 RAF 节流 / `d3-shape` 画 sparkline
- **测试**：pytest 现有 255 用例 + 新增 `test_streaming.py`（约 12 用例）；前端 vitest 新增 `LiveTokenCounter.spec.ts`（约 6 用例）

## Global Constraints（继承 agent.md §9）

- **禁止打印/提交 `config.json`**；测试用 `tmp_path`
- **不要批量转换行尾符**；改动验证用 `git diff -w`
- **每个任务落地后**：后端跑 `python -m pytest`、前端跑 `cd frontend && npm run build`
- **任何任务落地后必须同步更新 `agent.md` 对应章节**（统一在 Phase 4 收口）
- **不启动常驻服务**；commit 信息用中文一行式
- **桌面端在跑时仅改代码不重启**，Phase 2 验证需用户确认后重启

---

## Design Decisions（5 个关键决策）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| **1** | 全量改 vs 灰度 | **灰度** | 风险最低：先改 `_call_llm_reconciliation`（最痛）跑通稳定，再扩到 summary / final / analyzer |
| **2** | 实时 token 估算 | **混合** | 估算给动画用（`字符数 / 4 ≈ token`），最后 chunk 真实 usage 校准；前端收到"最终值"时把估算值对齐 |
| **3** | 流式截断时部分输出处理 | **保留 + 残缺落盘** | 累积 `full_content` 到中断点 → 9 级 JSON 容错链尝试解析 → 成功则 `recon_partial.json` 落盘（标记 partial=true）→ 下次启动检测 partial 文件则增量补全 |
| **4** | WebSocket 频率 | **服务端 150ms 节流** | 流式 chunk 50-100/s；服务端按 channel 合并最新值；前端 RAF 平滑插值；总开销 ≈ 6-7/s/连接 |
| **5** | 现有测试兼容 | **保留 `chat_with_retry`** | 旧 API 不删；新 API `chat_stream_with_retry` 并行存在；老测试 0 改动，新测试只覆盖流式分支 |

---

## Phase 1：流式骨架（0.5 天）

> 后端流式 API 单元测试跑通，不接业务。

### Task 1.1: 数据结构定义

**Files:**
- Modify: `backend/core/llm_client.py`（在文件顶部 dataclass 区域加 `StreamChunk` / `StreamResult`）

**Step 1.1.1: 新增 dataclass**

```python
@dataclass
class StreamChunk:
    """流式响应的一个 chunk"""
    type: str  # "content" | "reasoning" | "usage" | "tool_call" | "error"
    text: str = ""
    reasoning_text: str = ""
    # OpenAI 流式最后一个 chunk 才有；Anthropic 每个 chunk 都有
    usage_prompt_tokens: int = 0
    usage_completion_tokens: int = 0
    usage_cached_tokens: int = 0
    # 估算（基于字符数 / 4），给前端动画用
    estimated_total_tokens: int = 0
    # 错误信息（type=error 时）
    error: str = ""
    # 终止标志（type=usage 时可能为 True）
    is_final: bool = False


@dataclass
class StreamResult:
    """流式调用结束后的最终结果（与 chat() 返回对齐）"""
    success: bool
    content: str
    error: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    reasoning_chars: int  # 思考链总字符数（被剥离的）
    call_stats: dict     # 重试次数、最终温度等
```

- [ ] 写完 dataclass 后跑 `python -c "from backend.core.llm_client import StreamChunk, StreamResult; print('ok')"` 验证 import

### Task 1.2: 流式 chat 解析器

**Files:**
- Modify: `backend/core/llm_client.py`（新增 `chat_stream()` 异步生成器；不改旧 `chat()`）

**Step 1.2.1: 实现 `chat_stream()`**

```python
async def chat_stream(
    self,
    messages: List[dict],
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
) -> AsyncIterator[StreamChunk]:
    """流式 LLM 调用；累积每个 chunk 直到调用方中断或服务端关闭。
    
    与 chat() 区别：
    - 不用 asyncio.wait + hard_timeout 包裹（SDK 内部处理 stream 超时）
    - 立即 yield 每个 chunk 给调用方
    - usage 字段：OpenAI 在最后一个 chunk，Anthropic 每个 chunk
    """
    if max_tokens is None:
        max_tokens = self.config.max_tokens

    extra_kwargs = {}
    if self.provider != "anthropic" and self.config.json_mode != "default":
        if "localhost" not in self.config.base_url and "127.0.0.1" not in self.config.base_url:
            extra_kwargs["response_format"] = {"type": "json_object"}
    if self.config.thinking_mode and self.provider != "anthropic":
        extra_kwargs["extra_body"] = dict(self.config.thinking_mode)

    try:
        if self.provider == "anthropic":
            async with self.anthropic.messages.stream(
                **self._build_anthropic_payload(messages, temperature, max_tokens)
            ) as stream:
                async for event in stream:
                    chunk = self._parse_anthropic_stream_event(event)
                    if chunk:
                        yield chunk
                # 收尾 usage
                if stream.current_message_snapshot.usage:
                    u = stream.current_message_snapshot.usage
                    yield StreamChunk(
                        type="usage",
                        usage_prompt_tokens=u.input_tokens or 0,
                        usage_completion_tokens=u.output_tokens or 0,
                        usage_cached_tokens=u.cache_read_input_tokens or 0,
                        is_final=True,
                    )
        else:
            stream = await self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=self.config.timeout,
                stream=True,
                stream_options={"include_usage": True},  # OpenAI 强制每个 chunk 带 usage
                **extra_kwargs,
            )
            async for event in stream:
                chunk = self._parse_openai_stream_event(event)
                if chunk:
                    yield chunk
    except asyncio.CancelledError:
        # 调用方主动取消（如 stop 按钮）
        raise
    except Exception as e:
        yield StreamChunk(type="error", error=str(e))
```

**Step 1.2.2: 实现两个 provider 的 chunk 解析器**

```python
def _parse_openai_stream_event(self, event) -> Optional[StreamChunk]:
    """OpenAI 流式事件 → StreamChunk（剥离 <think> 标签，估算 token）"""
    # 跳过无 choices 的 usage-only chunk
    if not getattr(event, "choices", None):
        if getattr(event, "usage", None):
            return StreamChunk(
                type="usage",
                usage_prompt_tokens=event.usage.prompt_tokens or 0,
                usage_completion_tokens=event.usage.completion_tokens or 0,
                usage_cached_tokens=(
                    getattr(event.usage.prompt_tokens_details, "cached_tokens", 0) or 0
                ),
                is_final=True,
            )
        return None

    choice = event.choices[0]
    delta = getattr(choice, "delta", None)
    if not delta:
        return None

    text = getattr(delta, "content", "") or ""
    reasoning = getattr(delta, "reasoning_content", "") or ""

    # 估算 token：字符数 / 4（中文场景下偏保守，误差 ±20%）
    est_tokens = (len(text) + len(reasoning)) // 4

    if reasoning:
        return StreamChunk(type="reasoning", reasoning_text=reasoning, estimated_total_tokens=est_tokens)
    if text:
        return StreamChunk(type="content", text=text, estimated_total_tokens=est_tokens)
    return None


def _parse_anthropic_stream_event(self, event) -> Optional[StreamChunk]:
    """Anthropic 流式事件 → StreamChunk"""
    etype = getattr(event, "type", "")
    if etype == "content_block_delta":
        delta = getattr(event, "delta", None)
        if delta and getattr(delta, "type", "") == "text":
            text = getattr(delta, "text", "") or ""
            return StreamChunk(type="content", text=text, estimated_total_tokens=len(text) // 4)
        if delta and getattr(delta, "type", "") == "thinking":
            thinking = getattr(delta, "thinking", "") or ""
            return StreamChunk(type="reasoning", reasoning_text=thinking, estimated_total_tokens=len(thinking) // 4)
    elif etype == "message_delta":
        # Anthropic 在 message_delta 事件带 stop_reason + usage
        usage = getattr(event, "usage", None)
        if usage:
            return StreamChunk(
                type="usage",
                usage_completion_tokens=getattr(usage, "output_tokens", 0) or 0,
                is_final=False,
            )
    return None
```

- [ ] 实现 `chat_stream()` + 两个 `_parse_*_stream_event()`
- [ ] 单测：mock OpenAI/Anthropic 流式响应，验证 chunk 累积、reasoning 剥离、usage 提取
- [ ] 测试文件：`backend/tests/test_streaming.py::TestChatStream::test_openai_basic`

### Task 1.3: 流式重试包装

**Files:**
- Modify: `backend/core/llm_client.py`（新增 `chat_stream_with_retry()`；不改旧 `chat_with_retry`）

**Step 1.3.1: 实现 `chat_stream_with_retry()`**

```python
async def chat_stream_with_retry(
    self,
    messages: List[dict],
    max_tokens: Optional[int] = None,
    validate_response: Optional[Callable[[str], Awaitable[Tuple[bool, str]]]] = None,
    retry_messages_builder: Optional[Callable[[str, List[dict]], Awaitable[List[dict]]]] = None,
    on_progress: Optional[Callable[[StreamChunk], Awaitable[None]]] = None,
) -> StreamResult:
    """流式重试包装：温度退火 → 指数退避；保留 chat_with_retry 行为契约。
    
    on_progress 回调用于广播 token_delta（避免耦合 ProgressHub）。
    validate_response 失败时用 retry_messages_builder 注入 hint 重试。
    """
    last_error = "未知错误"
    call_stats = {"attempts": 0, "final_temperature": self.config.temperature}
    
    for attempt in range(self.config.temperature_max_retries + 1):
        new_temp = max(0, self.config.temperature - attempt * self.config.temperature_step)
        call_stats["attempts"] = attempt + 1
        call_stats["final_temperature"] = new_temp
        
        full_content = ""
        full_reasoning = ""
        last_usage = None
        
        try:
            async for chunk in self.chat_stream(messages, temperature=new_temp, max_tokens=max_tokens):
                if chunk.type == "content":
                    full_content += chunk.text
                elif chunk.type == "reasoning":
                    full_reasoning += chunk.reasoning_text
                elif chunk.type == "usage":
                    last_usage = chunk
                elif chunk.type == "error":
                    last_error = chunk.error
                    break
                
                # 进度回调（让调用方广播 token_delta）
                if on_progress:
                    await on_progress(chunk)
            
            if last_error != "未知错误":
                # 整个调用失败，进重试
                await self._sleep_backoff(attempt, last_error)
                continue
            
            # 成功累积完，剥离思考链
            stripped = self._strip_thinking(full_content)
            if stripped:
                full_content = stripped
            
            # 验证
            if validate_response:
                is_valid, err = await validate_response(full_content)
                if not is_valid:
                    last_error = f"验证失败: {err}"
                    if retry_messages_builder:
                        messages = await retry_messages_builder(err, messages)
                    await self._sleep_backoff(attempt, last_error)
                    continue
            
            # 成功
            return StreamResult(
                success=True,
                content=full_content,
                error="",
                prompt_tokens=last_usage.usage_prompt_tokens if last_usage else 0,
                completion_tokens=last_usage.usage_completion_tokens if last_usage else len(full_content) // 4,
                cached_tokens=last_usage.usage_cached_tokens if last_usage else 0,
                reasoning_chars=len(full_reasoning),
                call_stats=call_stats,
            )
        except asyncio.CancelledError:
            # 用户停止：返回部分结果（标记 cancelled）
            return StreamResult(
                success=False,
                content=full_content,  # 保留已生成内容
                error="用户请求停止",
                prompt_tokens=last_usage.usage_prompt_tokens if last_usage else 0,
                completion_tokens=last_usage.usage_completion_tokens if last_usage else len(full_content) // 4,
                cached_tokens=last_usage.usage_cached_tokens if last_usage else 0,
                reasoning_chars=len(full_reasoning),
                call_stats=call_stats,
            )
        except Exception as e:
            last_error = f"流式调用异常: {e}"
            await self._sleep_backoff(attempt, last_error)
            continue
    
    # 所有重试都失败
    return StreamResult(
        success=False,
        content=full_content,  # 保留最后一份 partial 内容供 9 级容错链尝试
        error=last_error,
        prompt_tokens=0, completion_tokens=0, cached_tokens=0,
        reasoning_chars=0, call_stats=call_stats,
    )
```

- [ ] 实现 `chat_stream_with_retry()` + `_sleep_backoff()` 复用
- [ ] 单测：mock 重试链（温度退火路径、验证失败重试路径）
- [ ] 测试文件：`backend/tests/test_streaming.py::TestChatStreamWithRetry::test_retry_on_validation_failure`

### Task 1.4: ProgressHub 加 token_delta 消息

**Files:**
- Modify: `backend/progress_hub.py`

**Step 1.4.1: 加 `token_delta` 消息类型 + 节流广播器**

```python
class ThrottledBroadcaster:
    """按 channel 合并最新值，150ms 内只发一次；适合流式 token 增量广播"""
    MIN_INTERVAL = 0.15  # 秒

    def __init__(self):
        self._last_emit: Dict[str, float] = {}
        self._latest: Dict[str, dict] = {}
        self._pending_tasks: Dict[str, asyncio.Task] = {}

    async def emit(self, channel: str, data: dict) -> None:
        self._latest[channel] = data
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_emit.get(channel, 0)
        if elapsed >= self.MIN_INTERVAL:
            await self._flush(channel)
        elif channel not in self._pending_tasks:
            self._pending_tasks[channel] = asyncio.create_task(self._delayed_flush(channel))

    async def _delayed_flush(self, channel: str):
        await asyncio.sleep(self.MIN_INTERVAL)
        if channel in self._latest:
            await self._flush(channel)
        self._pending_tasks.pop(channel, None)

    async def _flush(self, channel: str):
        if channel in self._latest:
            await self.broadcast(self._latest.pop(channel))
            self._last_emit[channel] = asyncio.get_event_loop().time()


# 在 ProgressHub 类中
class ProgressHub:
    def __init__(self):
        ...
        self._token_throttler = ThrottledBroadcaster()
    
    async def broadcast_token_delta(
        self,
        context: str,         # "analysis" | "summary_phase_1" | "summary_phase_2" | ...
        session_id: str,
        delta: dict,           # {output_tokens, rate_tokens_per_sec, elapsed_sec, eta_sec}
    ) -> None:
        """流式 token 增量广播（自动节流 5-10/s）"""
        msg = {
            "type": "token_delta",
            "context": context,
            "session_id": session_id,
            "delta": delta,
            "timestamp": time.time(),
        }
        await self._token_throttler.emit(f"{context}:{session_id}", msg)
```

- [ ] 实现 `ThrottledBroadcaster` + `ProgressHub.broadcast_token_delta()`
- [ ] 单测：mock 100 chunk/s 涌入，断言实际发出 ≤ 10 条/s
- [ ] 测试文件：`backend/tests/test_progress_hub.py::TestTokenDeltaThrottle`

### Phase 1 验收

- [ ] `backend/tests/test_streaming.py` 全部通过（≥8 用例）
- [ ] `backend/tests/test_progress_hub.py` 全部通过（≥1 新用例）
- [ ] 现有 255 用例 0 回归
- [ ] 桌面端不重启也能跑（流式代码路径暂不接入）

---

## Phase 2：接入 reconciliation 验证（0.5 天）

> 第一个真实业务场景，灰度起点。需用户授权重启桌面端验证。

### Task 2.1: `_call_llm_reconciliation` 改流式

**Files:**
- Modify: `backend/services/final_summary.py`（`_call_llm_reconciliation` 方法）

**Step 2.1.1: 流式包装 + 残缺 JSON 容错**

```python
async def _call_llm_reconciliation(self, prompt: str, batch_idx: int) -> Optional[dict]:
    """第二次调用：产出伏笔 reconciliation JSON（流式版）"""
    messages = [
        {"role": "system", "content": RECONCILIATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    await self._acquire_llm_slot()
    started_at = time.monotonic()
    
    async def on_progress(chunk: StreamChunk):
        if chunk.type in ("content", "usage"):
            elapsed = time.monotonic() - started_at
            await progress_hub.broadcast_token_delta(
                context="summary_phase_1",
                session_id=self.book_id,
                delta={
                    "batch_idx": batch_idx,
                    "output_tokens": chunk.estimated_total_tokens or 0,
                    "rate_tokens_per_sec": (chunk.estimated_total_tokens or 0) / max(elapsed, 0.1),
                    "elapsed_sec": elapsed,
                },
            )
    
    try:
        result = await self._llm.chat_stream_with_retry(
            messages,
            max_tokens=self.config.api.max_tokens,
            validate_response=validate_reconciliation_json,
            retry_messages_builder=build_reconciliation_retry_messages,
            on_progress=on_progress,
        )
    finally:
        self._release_llm_slot()
    
    self._record_tokens("reconciliation", (result.prompt_tokens, result.completion_tokens))
    
    if not result.success:
        logger.warning(f"伏笔 reconciliation 调用失败: {result.error}")
        # 即使失败，也尝试从残缺 content 解析（流式中断时常用）
        if result.content:
            partial = extract_foreshadow_json(result.content)
            if partial and partial.get("reconciliation"):
                logger.info(f"残缺 JSON 解析成功: {len(partial['reconciliation'])} 条（partial）")
                # 落盘标记 partial=True
                self._save_partial_reconciliation(batch_idx, partial)
                return partial
        return None
    
    return extract_foreshadow_json(result.content)
```

**Step 2.1.2: 落盘 `recon_partial.json`**

```python
def _save_partial_reconciliation(self, batch_idx: int, data: dict) -> None:
    """残缺 reconciliation 落盘：下次启动时检测到则增量补全（不覆盖已有 recon_N.json）"""
    checkpoint_dir = self._checkpoint_dir()  # 沿用现有 checkpoint 路径
    partial_path = checkpoint_dir / f"recon_{batch_idx}_partial.json"
    data["_partial"] = True
    data["_saved_at"] = datetime.now().isoformat()
    safe_save_json(partial_path, data)
    logger.info(f"残缺 reconciliation 已落盘: {partial_path}")
```

- [ ] 改 `_call_llm_reconciliation` 为流式
- [ ] 加 `_save_partial_reconciliation()`
- [ ] 单测：mock 流式响应验证 token_delta 广播、partial 落盘
- [ ] 测试文件：`backend/tests/test_streaming.py::TestFinalSummaryStreaming::test_reconciliation_streaming`
- [ ] **真实环境验证**：重启桌面端 + 跑 1 本书总结 → 观察 token_delta WebSocket 消息

### Task 2.2: 用户授权重启桌面端

**对话里向用户确认**：
1. 是否现在重启（phase=report 阶段可能快完成）
2. 重启后跑哪本书验证（建议《我不可能是剑神》当前正在跑的那本）
3. 验证指标：WebSocket 收到 `token_delta` 消息 + 真实进度 + 无 HardTimeout 增加

- [ ] 用户确认后 `taskkill /IM python.exe /F && run_desktop.bat`
- [ ] 重启后跑总结，观察：
  - 后端日志有 `token_delta` 广播记录
  - 桌面端 `crash.log` 不再涨到失控
  - 总结任务成功率 / 平均完成时间对比
- [ ] **若发现问题** → 修复；**若正常** → 进入 Phase 3

### Phase 2 验收

- [ ] `_call_llm_reconciliation` 流式工作
- [ ] 真实环境跑 1 本书无新增 HardTimeout
- [ ] 残缺 JSON 落盘机制可用
- [ ] 现有 255 用例 0 回归

---

## Phase 3：全面接入 + 前端（1.5 天）

### Task 3.1: 其他 `_call_llm_*` 改流式

**Files:**
- Modify: `backend/services/final_summary.py`（`_call_llm_summary` / `_call_llm_final` / `_call_llm_global_recheck`）

按 Task 2.1 同样的模式改：
- `_call_llm_summary` → context="summary_phase_1"（与 reconciliation 共用）
- `_call_llm_global_recheck` → context="summary_phase_2"
- `_call_llm_final` → context="summary_phase_4"
- `_run_style_extraction` → context="summary_phase_3"

- [ ] 4 个方法全部改流式
- [ ] 每个加 `on_progress` 回调
- [ ] 单测覆盖 4 个方法

### Task 3.2: pipeline.py 分析阶段流式接入

**Files:**
- Modify: `backend/core/pipeline.py`（`_analyze_one_block` 调 `chat_stream_with_retry`）

```python
async def _analyze_one_block(self, block, ...):
    ...
    async def on_progress(chunk: StreamChunk):
        await progress_hub.broadcast_token_delta(
            context="analysis",
            session_id=self.book_id,
            delta={
                "block_idx": block.idx,
                "output_tokens": chunk.estimated_total_tokens or 0,
                ...
            },
        )
    
    result = await self.analyzer._llm.chat_stream_with_retry(
        messages, max_tokens=self.config.api.max_tokens,
        validate_response=validate_analysis_json,
        retry_messages_builder=build_analysis_retry_messages,
        on_progress=on_progress,
    )
    ...
```

- [ ] `pipeline.py._analyze_one_block` 改流式
- [ ] 上下文广播 `context="analysis"`
- [ ] 单测：mock 分析阶段流式

### Task 3.3: 前端 WebSocket 订阅 token_delta

**Files:**
- Modify: `frontend/src/composables/useProgressSocket.ts`（加 `tokenDelta` 订阅）
- Modify: `frontend/src/api/client.ts`（加 `TokenDelta` DTO）

```typescript
// client.ts
export interface TokenDelta {
  type: 'token_delta'
  context: 'analysis' | 'summary_phase_1' | 'summary_phase_2' | 'summary_phase_3' | 'summary_phase_4'
  session_id: string
  delta: {
    output_tokens: number
    rate_tokens_per_sec: number
    elapsed_sec: number
    eta_sec?: number
    batch_idx?: number
    block_idx?: number
  }
  timestamp: number
}

// useProgressSocket.ts
const tokenDeltaSubscribers = new Set<(delta: TokenDelta) => void>()

export function onTokenDelta(cb: (delta: TokenDelta) => void): () => void {
  tokenDeltaSubscribers.add(cb)
  return () => tokenDeltaSubscribers.delete(cb)
}

// 在 WS message handler 中：
} else if (msg.type === 'token_delta') {
  tokenDeltaSubscribers.forEach(cb => cb(msg as TokenDelta))
}
```

- [ ] 加 DTO + 订阅 API
- [ ] 单测：vitest 覆盖订阅 / 取消订阅

### Task 3.4: `LiveTokenCounter.vue` 新组件

**Files:**
- Create: `frontend/src/components/LiveTokenCounter.vue`

**功能**：
- 大数字显示当前 output_tokens（用 RAF 平滑插值到目标值）
- tokens/s 速率（带颜色：>300 绿、100-300 黄、<100 红）
- 进度环（基于"当前 / 上次完整调用 token 数"——首版用 last_final 缓存）
- sparkline 速率曲线（最近 30 秒，d3-shape）
- ETA（基于速率 + 目标 token 数）

```vue
<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { onTokenDelta, type TokenDelta } from '@/composables/useProgressSocket'
import { line, curveMonotoneX } from 'd3-shape'

const props = defineProps<{
  context: 'analysis' | 'summary_phase_1' | 'summary_phase_2' | 'summary_phase_3' | 'summary_phase_4'
  bookId: string
}>()

const targetOutput = ref(0)
const displayOutput = ref(0)
const rate = ref(0)
const elapsed = ref(0)
const rateHistory = ref<number[]>([])  // 最近 30 秒
let raf: number | null = null
let lastUpdate = 0

function animate() {
  // RAF 插值：displayOutput 平滑追 targetOutput
  const diff = targetOutput.value - displayOutput.value
  displayOutput.value += diff * 0.18
  if (Math.abs(diff) < 0.5) displayOutput.value = targetOutput.value
  raf = requestAnimationFrame(animate)
}

const unsubscribe = onTokenDelta((delta) => {
  if (delta.context !== props.context || delta.session_id !== props.bookId) return
  targetOutput.value = delta.delta.output_tokens
  rate.value = delta.delta.rate_tokens_per_sec
  elapsed.value = delta.delta.elapsed_sec
  
  // sparkline 历史
  const now = Date.now()
  if (now - lastUpdate > 1000) {  // 1s 采样一次
    rateHistory.value.push(rate.value)
    if (rateHistory.value.length > 30) rateHistory.value.shift()
    lastUpdate = now
  }
})

onMounted(() => { raf = requestAnimationFrame(animate) })
onUnmounted(() => { 
  if (raf) cancelAnimationFrame(raf)
  unsubscribe()
})

const rateColor = computed(() => {
  if (rate.value > 300) return 'text-green-500'
  if (rate.value > 100) return 'text-yellow-500'
  return 'text-red-500'
})
</script>

<template>
  <div class="flex flex-col gap-2 p-3 rounded bg-white/5">
    <div class="flex items-baseline gap-3">
      <span class="text-2xl font-mono tabular-nums">{{ Math.round(displayOutput).toLocaleString() }}</span>
      <span class="text-sm text-gray-500">tokens</span>
      <span :class="['ml-auto text-sm', rateColor]">{{ rate.toFixed(0) }} tok/s</span>
    </div>
    <Sparkline :data="rateHistory" :width="240" :height="32" />
    <div class="text-xs text-gray-500">已用 {{ elapsed.toFixed(1) }}s</div>
  </div>
</template>
```

- [ ] 实现 `LiveTokenCounter.vue` + 子组件 `Sparkline.vue`
- [ ] 单元测试 `LiveTokenCounter.spec.ts`（6 用例：基本渲染、动画插值、订阅/取消、context 过滤、颜色规则）
- [ ] 跑 `npm run build` 验证

### Task 3.5: 集成到 QueuePage 和 SummaryPage

**Files:**
- Modify: `frontend/src/pages/QueuePage.vue`（顶部 session stats 区）
- Modify: `frontend/src/pages/SummaryPage.vue`（4 阶段卡）

**QueuePage 集成**：
```vue
<LiveTokenCounter
  v-if="running && currentItem"
  context="analysis"
  :book-id="currentItem.id"
/>
```

**SummaryPage 集成**：
```vue
<div v-for="phase in phases" :key="phase.id">
  <PhaseCard :phase="phase">
    <template #live>
      <LiveTokenCounter
        v-if="phase.isRunning"
        :context="phase.context"
        :book-id="bookId"
      />
    </template>
  </PhaseCard>
</div>
```

- [ ] 集成到 QueuePage（启动分析后显示）
- [ ] 集成到 SummaryPage（4 阶段卡分别显示）
- [ ] 跑 `npm run build` + `vue-tsc --noEmit`

### Phase 3 验收

- [ ] 所有 `_call_llm_*` + `_analyze_one_block` 全部流式
- [ ] 前端 `LiveTokenCounter` 实时显示 token / 速率 / sparkline
- [ ] `npm run build` 0 错
- [ ] 真实环境跑 1 本书端到端，桌面端数字真实增长

---

## Phase 4：测试 + 文档（0.5 天）

### Task 4.1: 测试覆盖

- [ ] `backend/tests/test_streaming.py` 总计 ≥12 用例
- [ ] `frontend/src/components/__tests__/LiveTokenCounter.spec.ts` ≥6 用例
- [ ] 跑 `python -m pytest` + `cd frontend && npm test`：0 回归
- [ ] 跑 `cd frontend && npm run build`：0 错
- [ ] 真实环境端到端：跑 1 本书总结 + 1 本书分析，记录时间 / 超时数 / 体验

### Task 4.2: 文档同步

**Files:**
- Modify: `agent.md`（§10.18 后加 §10.19 H16）
- Modify: `CHANGELOG.md`（加 `## 2026-08-26 H16 流式 LLM + 实时 token 动画`）
- Modify: `README.md`（更新"实时进度"章节、加"流式 token"图示）

**agent.md §10.19 要点**：
- 根因：30 个 HardTimeout 15% + M2.7 思考模型 5-6 分钟生成时间 + 10.5 分钟硬超时边缘
- 修复：流式 chat + on_progress 回调 + ProgressHub 节流广播 + LiveTokenCounter
- 数据：reconciliation 18k token 输出从 5-6 分钟用户感知"卡死" → 实时速率 220 tok/s 可见
- 收益：HardTimeout 预期 < 5%（不彻底归零，因为服务端仍可能 0 字节响应）

### Task 4.3: 提交

- [ ] `git add` 仅本次新增/修改文件（**严格不混入其他 Agent 未提交改动**）
- [ ] `git commit -m "feat(streaming): H16 流式 LLM + 实时 token 动画"`
- [ ] commit 信息用中文一行式
- [ ] 验证 commit 只含本次改动

### Phase 4 验收

- [ ] 测试 / 文档 / 提交 全部完成
- [ ] 现有 255 用例 + 新增 ≥18 用例全过
- [ ] 真实环境 demo 视频或截图（可选）

---

## Risk Matrix

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| 1 | OpenAI 流式 usage 字段位置不一致 | 高 | 中 | `stream_options={"include_usage": True}` 强制；兜底用字符/4 估算 |
| 2 | Anthropic `messages.stream` API 与 sync 不同 | 中 | 中 | Task 1.2.2 用 `async with` + `current_message_snapshot` 处理 |
| 3 | 思考型模型流式下 thinking 是单独 chunk | 中 | 低 | `delta.reasoning_content` 与 `delta.content` 分流；thinking 不计入 output_tokens（仅提示用） |
| 4 | 流式中断 → partial JSON 解析失败 | 高 | 中 | 复用 9 级容错链 `json_utils.parse_json_robust`；落 `recon_partial.json` 不覆盖已有 |
| 5 | WebSocket 高频 → 桌面端卡顿 | 中 | 中 | 服务端 150ms 节流 + 客户端 RAF + 仅窗口可见时刷新 |
| 6 | CountUp 动画在流式下不收敛 | 中 | 低 | 加"target value 模式"+ 收敛阈值 0.5 token；最终值对齐时强制跳到目标 |
| 7 | 现有测试 fixture 假设 chat() 返回 Tuple | 高 | 低 | Task 5：保留 chat() / chat_with_retry 旧 API；新 API 并行存在；旧测试 0 改动 |
| 8 | 总结 4 阶段并发流式抢 ProgressHub | 中 | 中 | 每个阶段独立 `context` 字段 + 独立 throttler key |
| 9 | 桌面端 webview 缓存旧 build | 中 | 低 | 走 `npm run build` 重新打包；桌面 `?v=timestamp` 已禁缓存 |
| 10 | 流式响应里 API Key 泄露（错误消息） | 低 | 高 | `_parse_openai_stream_event` 严格 strip，不向外 yield 含 key 字段的 chunk |

## Acceptance Criteria（全局）

1. **后端流式 API 可用**：`chat_stream_with_retry` 通过 12+ 单测
2. **业务全接入**：所有 `_call_llm_*` + `_analyze_one_block` 走流式
3. **真实环境**：
   - HardTimeout 数从 30/200 (15%) 降到 <10/200 (<5%)
   - 用户感知"卡死" → 可见实时速率
4. **前端体验**：
   - QueuePage 启动分析后能看到 LiveTokenCounter
   - SummaryPage 4 阶段卡独立显示 token / 速率 / sparkline
   - 数字真实增长（不是 5s 轮询）
5. **断点续跑无回归**：总结阶段 checkpoint 仍能正常续跑；分析阶段块级重试无回归
6. **测试 + 文档 + commit** 全清：255 + ≥18 = 273 用例；agent.md §10.19 + CHANGELOG + README

## Test Plan

### 单元测试

- `backend/tests/test_streaming.py`（12 用例）
  - `TestChatStream`（4）：OpenAI 基础流、Anthropic 基础流、thinking 剥离、usage 提取
  - `TestChatStreamWithRetry`（4）：温度退火重试、验证失败重试、用户取消（保留 partial）、全失败返回空
  - `TestFinalSummaryStreaming`（2）：reconciliation 流式、partial 落盘
  - `TestProgressHubThrottle`（2）：150ms 节流、合并最新值
- `frontend/src/components/__tests__/LiveTokenCounter.spec.ts`（6 用例）
  - 基本渲染、订阅 / 取消、context 过滤、速率颜色、RAF 收敛、卸载清理

### 集成测试

- `backend/tests/test_integration_streaming.py`（1 端到端）
  - mock 整个 OpenAI SDK 流式响应 + 验证 WebSocket 广播

### 真实环境

- 跑 1 本书总结（《我不可能是剑神》5 批），对比：
  - HardTimeout 数（应下降）
  - 完成时间（应不显著增加）
  - token 累计（应一致）
- 跑 1 本书分析（任意 100 章），对比：
  - 完成时间
  - 桌面端 LiveTokenCounter 显示是否正常

## Documentation Updates

| 文件 | 章节 | 改动 |
|---|---|---|
| `agent.md` | §10.19 | 新增 H16 条目（按现有 H1-H18 格式） |
| `CHANGELOG.md` | 阶段四 | 加 `## 2026-08-26 H16 流式 LLM + 实时 token 动画` 章节 |
| `README.md` | 实时进度 | 更新"WebSocket 广播"段落、加流式示意图、移除"5s 轮询"描述 |
| `frontend/src/components/LiveTokenCounter.vue` | - | 顶部加 component doc（KDoc 风格） |

> ✅ **已批准 Decisions 见 §Plan Revisions 顶部**（Open Questions 已合并到 Decisions 表格）

## Timeline

- **Phase 1**（流式骨架）：0.5 天
- **Phase 2**（reconciliation 灰度）：0.5 天（含用户授权重启等待）
- **Phase 3**（全面接入 + 前端）：1.5 天
- **Phase 4**（测试 + 文档）：0.5 天
- **总计**：3 天

## References

- OpenAI 流式文档：https://platform.openai.com/docs/api-reference/chat-streaming
- Anthropic 流式文档：https://docs.anthropic.com/en/api/messages-streaming
- 项目 WebSocket 现有消息类型：`backend/progress_hub.py`（log / progress / block_done / state_change / token_stats / summary_progress）
- 现有 `CountUp.vue` 组件：`frontend/src/components/CountUp.vue`
- 9 级 JSON 容错链：`backend/utils/json_utils.py::parse_json_robust`
- H15 日志轮转修复 commit：`9f8f31d`（刚刚提交）

---

## Plan Revisions（2026-08-26 复审补丁）

> **来源**：用户在批准前对 plan 做了逐项代码核对（不是纸面审查），挑出 4 个 P0 + 4 个 P1 + 7 个小问题 + 1 个架构决策修订。本节是**集中修订清单**，按"问题→修订后代码→应用到哪个 Task"组织。实施时按本节改主 plan 内的对应 Task 即可。
>
> **应用方式**：用本节代码替换主 plan 内对应 Task 的伪代码段；或在实施时同时读两边。

### 已批准 Decisions（替换原 Open Questions）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 分支策略 | 建 `feature/streaming-tokens` 分支（独立 commit 链，可单独回滚） |
| 2 | Phase 2 重启时机 | 等当前 `phase=report` 跑完再重启（避免打断） |
| 3 | 验证用书 | 另开一本短书（避免污染《我不可能是剑神》当前任务数据） |
| 4 | 回滚开关 | **必做**：`config.json` 加 `analysis.streaming_enabled` + `summary.streaming_enabled`（默认 true；Phase 1 就落地；出问题能秒切回老链路） |
| 5 | WebSocket 频率 | 150ms 硬编码（用户感知不到，但代码简单） |
| 6 | WebView2 兼容 | 跳过（项目最低 WebView2 即当前 Windows 版本） |

---

### P0-1: 流式版必须支持 stop 语义（停止按钮会失效）

**问题**：`chat_stream_with_retry` 只在外部 `cancel()` 时响应，但 `request_stop()` 只 `set _stop_event` 不 cancel task（`llm_client.py:211-215`）。结果：用户点停止后，进行中的 5-6 分钟流式调用会跑完。

**核对证据**：`llm_client.py:904, 1018` 在每轮温度退火前都检查 `self._stop_requested`。

**修订**：在 `chat_stream` 和 `chat_stream_with_retry` 的 chunk 循环里**都**加 stop 检查。

```python
# 应用到 Task 1.2 chat_stream
async def chat_stream(self, messages, temperature=0.1, max_tokens=None) -> AsyncIterator[StreamChunk]:
    ...
    try:
        if self.provider == "anthropic":
            async with self.anthropic.messages.stream(**payload) as stream:
                async for event in stream:
                    # 关键：每 chunk 检查 stop（事件循环让步，不阻塞 stream）
                    if self._stop_requested:
                        await stream.close()  # 关闭 anthropic 流
                        return
                    chunk = self._parse_anthropic_stream_event(event)
                    if chunk:
                        yield chunk
                ...
        else:
            stream = await self.client.chat.completions.create(
                ..., stream=True, stream_options={"include_usage": True}
            )
            async for event in stream:
                # 关键：每 chunk 检查 stop
                if self._stop_requested:
                    await stream.close()  # OpenAI SDK 1.x 支持 close
                    return
                chunk = self._parse_openai_stream_event(event)
                if chunk:
                    yield chunk
    except asyncio.CancelledError:
        raise
    except Exception as e:
        yield StreamChunk(type="error", error=str(e))
```

**应用**：
- Task 1.2 chat_stream 主循环加 `if self._stop_requested: await stream.close(); return`
- Task 1.3 chat_stream_with_retry 内部循环同样检查（详见 P0-2）

---

### P0-2: chat_stream_with_retry 必须对齐 chat_with_retry 全部契约

**问题**：
- `last_error` 在主循环外初始化为 `"未知错误"`，**每次 attempt 失败时 `last_error != "未知错误"` 恒真**，下一轮即使成功也会被 `continue` 丢掉 → 重试链基本报废
- 丢了 `chat_with_retry` 完整契约（核对 `llm_client.py:877-1130`）：
  - 429 Retry-After 长退避（1006, 1008, 1038, 1039）
  - 指数退避（1014, 1016, 1041, 1044）
  - 审核短路 `moderation_hits >= 2`（973, 974, 1023）
  - 认证即停（1001, 1114）
  - FailureLogger 5 处记录（947, 975, 991, 1024, 1081, 1104）
  - `_failed_tokens` / `_attempts` 累加（940, 909, 1074, 1036）—— StatsPage 在用
  - `last_consumed_tokens` / `call_failed_tokens` 追踪（942, 988, 1076, 1102）
- `_sleep_backoff` 不存在（**只有 `_sleep` 在 873 行**）

**核对证据**：原 `chat_with_retry` 长达 254 行（877-1130），含两层 for 循环（温度退火 + 指数退避）。

**修订**：Task 1.3 chat_stream_with_retry 整段重写，**完全对齐** chat_with_retry 行为契约。

```python
# 完整重写 Task 1.3 chat_stream_with_retry
async def chat_stream_with_retry(
    self,
    messages: List[dict],
    max_tokens: Optional[int] = None,
    validate_response: Optional[Callable] = None,  # 同步或异步均可，详见 P0-3
    retry_messages_builder: Optional[Callable] = None,
    on_progress: Optional[Callable[[StreamChunk], Awaitable[None]]] = None,
) -> StreamResult:
    """流式重试包装：完整对齐 chat_with_retry 行为契约
    （温度退火 → 指数退避；429 Retry-After；审核短路；认证即停；FailureLogger；统计累加）。"""
    last_error = ""  # 关键：每次调用重置为 ""，不是 "未知错误"
    total_attempts = 0
    moderation_hits = 0
    call_failed_tokens = 0
    messages_length = len(str(messages))
    last_consumed_tokens: Tuple[int, int] = (0, 0)
    accumulated_output_tokens = 0  # P0-4 累计值
    started_at = time.monotonic()
    final_content = ""  # 保留 partial content
    final_reasoning = ""
    last_usage = None
    
    logger.info("=" * 60)
    logger.info(f"开始流式API调用 - Model: {self.config.model}")
    logger.info(f"配置: temperature={self.config.temperature}, ..., "
                f"backoff_max_retries={self.config.backoff_max_retries}")
    logger.info("=" * 60)
    
    # === 温度退火重试层 ===
    for attempt in range(self.config.temperature_max_retries):
        if self._stop_requested:
            logger.info("⛔ 检测到停止请求，终止重试链")
            return StreamResult(success=False, content=final_content, error="用户请求停止",
                                prompt_tokens=last_consumed_tokens[0], 
                                completion_tokens=last_consumed_tokens[1],
                                cached_tokens=0, reasoning_chars=len(final_reasoning),
                                call_stats={"attempts": total_attempts, "failed_tokens": call_failed_tokens})
        total_attempts += 1
        with self._stats_lock:
            self._attempts += 1
        
        temp = max(0.0, self.config.temperature - attempt * self.config.temperature_step)
        logger.info(f"\n[温度退火] 尝试 {total_attempts}，temperature={temp} (第{attempt + 1}/{self.config.temperature_max_retries}次)")
        
        # 调用流式 chat，累积 + 检查 stop + 失败处理
        attempt_content = ""
        attempt_reasoning = ""
        attempt_success = False
        attempt_error = ""
        attempt_tokens = (0, 0)
        
        try:
            async for chunk in self.chat_stream(messages, temperature=temp, max_tokens=max_tokens):
                if chunk.type == "content":
                    attempt_content += chunk.text
                    accumulated_output_tokens += len(chunk.text) // 4  # P0-4 累计
                elif chunk.type == "reasoning":
                    attempt_reasoning += chunk.reasoning_text
                elif chunk.type == "usage":
                    last_usage = chunk
                    attempt_tokens = (chunk.usage_prompt_tokens, chunk.usage_completion_tokens)
                elif chunk.type == "error":
                    attempt_error = chunk.error
                    break
                
                # P0-4: on_progress 传累计值（含 elapsed + rate）
                if on_progress:
                    elapsed = time.monotonic() - started_at
                    await on_progress(StreamChunk(
                        type="content",
                        estimated_total_tokens=accumulated_output_tokens,  # 累计
                        text=chunk.text if chunk.type == "content" else "",
                    ))
        except asyncio.CancelledError:
            return StreamResult(success=False, content=attempt_content, error="用户请求停止",
                                prompt_tokens=0, completion_tokens=0, cached_tokens=0,
                                reasoning_chars=0, call_stats={"attempts": total_attempts, "failed_tokens": call_failed_tokens})
        
        if attempt_error:
            last_error = attempt_error
            logger.warning(f"❌ 尝试失败: {attempt_error}")
            # 累计失败 tokens（如有 usage 返回）
            if attempt_tokens[0] > 0 or attempt_tokens[1] > 0:
                with self._stats_lock:
                    self._failed_tokens += attempt_tokens[0] + attempt_tokens[1]
                call_failed_tokens += attempt_tokens[0] + attempt_tokens[1]
                last_consumed_tokens = attempt_tokens
        else:
            # 流式 chunk 全部成功累积，剥离思考链
            stripped = self._strip_thinking(attempt_content)
            if stripped:
                attempt_content = stripped
            final_content = attempt_content
            final_reasoning = attempt_reasoning
            attempt_success = True
            
            # 累计成功 tokens
            if attempt_tokens[0] > 0 or attempt_tokens[1] > 0:
                with self._stats_lock:
                    self._total_tokens += attempt_tokens[0] + attempt_tokens[1] + (last_usage.usage_cached_tokens if last_usage else 0)
                    self._cached_tokens += last_usage.usage_cached_tokens if last_usage else 0
                last_consumed_tokens = attempt_tokens
            
            # 验证
            if validate_response:
                is_valid, validation_error = await _call_validate_response(
                    validate_response, attempt_content, timeout=30
                )
                if not is_valid:
                    logger.warning(f"❌ 响应验证失败: {validation_error}")
                    if retry_messages_builder:
                        messages = retry_messages_builder(validation_error, messages)
                    _get_failure_logger().record_failure(
                        attempt_num=total_attempts,
                        max_retries=self.config.temperature_max_retries + self.config.backoff_max_retries,
                        temperature=temp, error_type="ValidationFailed",
                        error_message=f"响应验证失败: {validation_error}",
                        wait_time=0, messages_length=messages_length
                    )
                    last_error = f"响应验证失败: {validation_error}"
                    if attempt < self.config.temperature_max_retries - 1:
                        await self._sleep(3 * (attempt + 1))
                    continue  # 重要：last_error 在此处被显式覆盖，不影响下次循环起点
            
            # 成功
            logger.info(f"✅ API请求成功（第{total_attempts}次尝试）")
            return StreamResult(
                success=True, content=attempt_content, error="",
                prompt_tokens=attempt_tokens[0], completion_tokens=attempt_tokens[1],
                cached_tokens=last_usage.usage_cached_tokens if last_usage else 0,
                reasoning_chars=len(attempt_reasoning),
                call_stats={"attempts": total_attempts, "failed_tokens": call_failed_tokens},
            )
        
        # 失败分支（attempt_error 或 验证失败）
        if "认证失败" in last_error or "401" in last_error:
            logger.error("⛔ 认证错误，停止重试")
            return StreamResult(success=False, content=final_content, error=last_error,
                                prompt_tokens=last_consumed_tokens[0], completion_tokens=last_consumed_tokens[1],
                                cached_tokens=0, reasoning_chars=len(final_reasoning),
                                call_stats={"attempts": total_attempts, "failed_tokens": call_failed_tokens})
        
        if is_moderation_error(last_error):
            if moderation_hits >= 2:
                _get_failure_logger().record_failure(...)
                logger.warning("⛔ 内容审核拦截（连续3次），跳过该章节")
                return StreamResult(success=False, content=final_content, error=last_error, ...)
            moderation_hits += 1
        
        _get_failure_logger().record_failure(
            attempt_num=total_attempts, max_retries=..., temperature=temp,
            error_type="RetryNeeded", error_message=last_error, wait_time=0, messages_length=messages_length
        )
        
        if attempt < self.config.temperature_max_retries - 1:
            if self._is_429(last_error):
                wait_time = min(self._retry_after_seconds(last_error) or 30 * (attempt + 1), 120)
            else:
                wait_time = 3 * (attempt + 1)
            await self._sleep(wait_time)
    
    # === 指数退避重试层（429 时额外多试 3 轮）===
    final_temp = max(0.0, self.config.temperature - (self.config.temperature_max_retries - 1) * self.config.temperature_step)
    backoff_rounds = max(self.config.backoff_max_retries, 3) if self._is_429(last_error) else self.config.backoff_max_retries
    for retry_num in range(backoff_rounds):
        if self._stop_requested:
            return StreamResult(success=False, content=final_content, error="用户请求停止", ...)
        if is_moderation_error(last_error) and moderation_hits >= 2:
            # 审核短路
            return StreamResult(success=False, content=final_content, error=last_error, ...)
        total_attempts += 1
        with self._stats_lock:
            self._attempts += 1
        
        if self._is_429(last_error):
            wait_time = min(self._retry_after_seconds(last_error) or 30 * (retry_num + 1), 120)
        else:
            wait_time = min(2 ** (retry_num + 1), 60)
        await self._sleep(wait_time)
        
        # ... 流式重试同温度退火层逻辑 ...
    
    final_msg = f"所有重试均失败（温度退火{self.config.temperature_max_retries}次 + 指数退避{self.config.backoff_max_retries}次 = 共{total_attempts}次）。最后错误: {last_error}"
    logger.error(final_msg)
    return StreamResult(success=False, content=final_content, error=final_msg, ...)


async def _call_validate_response(validate_fn, content, timeout=30):
    """兼容同步/异步 validate_response，30s 超时"""
    import inspect
    try:
        if inspect.iscoroutinefunction(validate_fn):
            return await asyncio.wait_for(validate_fn(content), timeout=timeout)
        else:
            return await asyncio.wait_for(asyncio.to_thread(validate_fn, content), timeout=timeout)
    except asyncio.TimeoutError:
        return False, "验证超时（30秒）"
    except Exception as e:
        return False, f"验证异常: {e}"
```

**应用**：
- Task 1.3 整段用上面代码替换原 plan 的简化版
- 关键修复：`last_error = ""`（不是 `"未知错误"`）作为初始值
- 新增 P0-4 累计值维护
- 补齐全部 chat_with_retry 契约

---

### P0-3: validate_response 必须兼容同步/异步

**问题**：plan 新签名 `Awaitable` 直接 `await`，会把现有同步 validator（`validate_reconciliation_json` 等）传进去直接 TypeError。

**核对证据**：
- `llm_client.py:881` 声明 `Callable[[str], Tuple[bool, str]]` 同步
- `validate_reconciliation_json` 在 `final_summary.py:438` 是同步函数
- 老代码用 `await asyncio.wait_for(asyncio.to_thread(validate_response, content), timeout=30)`（925, 1059）

**修订**：新增 `_call_validate_response()` 工具函数（见 P0-2 末尾代码），用 `inspect.iscoroutinefunction` 区分。

**应用**：
- Task 1.3 中所有 `validate_response` 调用点都用 `_call_validate_response()` 包装
- 保持现有同步 validator 0 改动

---

### P0-4: on_progress 必须传累计值（前端动画才能跑）

**问题**：`StreamChunk.estimated_total_tokens` 是单 chunk 的 `len/4`（几个 token），Task 2.1 直接把它当 `output_tokens` 广播，前端 `LiveTokenCounter` 当累计总数插值 → 数字在个位数抖动、速率计算全错。

**修订**：`chat_stream_with_retry` 内部维护 `accumulated_output_tokens`（详见 P0-2 修订代码），`on_progress` 回调时把**累计值**塞进 `StreamChunk.estimated_total_tokens` 字段。

**注意**：StreamChunk 数据结构不变（已定义 `estimated_total_tokens`），只是 on_progress 调用点的语义从"单 chunk"改为"累计"。

**应用**：Task 1.3 的 on_progress 调用点 + Task 2.1 接收端（无需改接收端，含义自然正确）。

---

### P1-5: Task 3.2 改错文件 + Task 3.1 漏 style_analyzer.py

**问题**：
- 分析阶段的 LLM 调用在 `analyzer.py:144`（`analyze_chapter`），不在 `pipeline._analyze_one_block`
- 实际用的是 `validate_json_response` 和 `build_retry_messages` 闭包（带 `parsed_cache` 副作用）
- plan 里虚构的 `validate_analysis_json` / `build_analysis_retry_messages` 不存在
- `_run_style_extraction` 委托给 `style_analyzer.py:394` `extract_style_profile`（独立 LLMClient + api_config dict），改流式必须动 style_analyzer.py

**核对证据**：
- `analyzer.py:144-149` 调 `chat_with_retry(messages, max_tokens, validate_response=validate_json_response, retry_messages_builder=build_retry_messages)`
- `style_analyzer.py:394` `async def extract_style_profile(blocks_dir, book_name, api_config, token_sink=None)`
- `style_analyzer.py:412` `await call_llm_semantic(semantic_prompt, api_config, token_sink=token_sink)`

**修订**：

**Task 3.2 改 analyzer.py**：

```python
# 应用到 Task 3.2: 改 backend/core/analyzer.py 的 analyze_chapter
# 找到 LLMClient.chat_with_retry 调用（约 144 行），替换为 chat_stream_with_retry

# 旧：
success, response, error, token_counts, call_stats = await self.llm_client.chat_with_retry(
    messages,
    max_tokens=self.config.api.max_tokens,
    validate_response=validate_json_response,
    retry_messages_builder=build_retry_messages
)

# 新：
async def on_progress(chunk: StreamChunk):
    if chunk.type in ("content", "usage"):
        await progress_hub.broadcast_token_delta(
            context="analysis", session_id=self.book_id,
            delta={
                "block_idx": block_id,  # 来自调用上下文
                "output_tokens": chunk.estimated_total_tokens,  # 累计值（P0-4）
                "rate_tokens_per_sec": chunk.estimated_total_tokens / max(time.monotonic() - block_started, 0.1),
                "elapsed_sec": time.monotonic() - block_started,
            },
        )

result = await self.llm_client.chat_stream_with_retry(
    messages,
    max_tokens=self.config.api.max_tokens,
    validate_response=validate_json_response,  # 同步函数，P0-3 兼容
    retry_messages_builder=build_retry_messages,  # 同步闭包
    on_progress=on_progress,
)
# 后续用 result.success / result.content / result.completion_tokens 替代旧 API 返回值
```

**注意**：闭包 `validate_json_response` / `build_retry_messages` 引用 `parsed_cache` 闭包变量保持不变（流式版只换外层调用 API，内层闭包逻辑 0 改动）。

**Task 3.1 加 style_analyzer.py**：

```python
# 应用到 Task 3.1: 改 backend/core/style_analyzer.py 的 call_llm_semantic
# 找到 call_llm_semantic 内部调 LLMClient 的位置，替换为流式

# 假设原有签名: async def call_llm_semantic(prompt, api_config, token_sink=None)
# 改为流式 + on_progress（注意：style_analyzer 不用 LLMClient 实例，自己构造）
async def on_progress(chunk: StreamChunk):
    await progress_hub.broadcast_token_delta(
        context="summary_phase_3", session_id=book_name,
        delta={"output_tokens": chunk.estimated_total_tokens, ...},
    )

# LLMClient 实例化（沿用现有逻辑）
llm = LLMClient(APIConfig(**api_config))
result = await llm.chat_stream_with_retry(
    [{"role": "user", "content": prompt}],
    max_tokens=api_config.get("max_tokens", 8192),
    on_progress=on_progress,
)
# 解析 + 后续处理同原代码
```

**应用**：
- Task 3.2 改 `analyzer.py:144` 改 `pipeline._analyze_one_block`
- Task 3.1 新增"改 style_analyzer.py"子任务（在 `_run_style_extraction` 之后追加）

---

### P1-6: 前端路径与依赖修正

**问题**：
- `useProgressSocket.ts` 在 `frontend/src/api/`，不在 `composables/`
- `d3-shape` / `@vueuse/core` **不在** `package.json` 里，plan 没列安装步骤
- 测试惯例是同目录 `.test.ts`（`TimelinePage.test.ts` / `useProgressSocket.test.ts`），不是 `__tests__/*.spec.ts`
- 现有消息信封是 `{type, payload}`，plan 的 token_delta 是平铺结构

**核对证据**：
- `frontend/src/api/useProgressSocket.ts`（实际路径）
- `frontend/src/api/useProgressSocket.test.ts`（同目录测试）
- `frontend/package.json` 仅有 `echarts` / `vue` / `vue-router` 三个运行时依赖
- `frontend/src/pages/TimelinePage.test.ts` / `composables/useLogStore.test.ts` 等

**修订**：

```typescript
// 应用到 Task 3.3: 改 client.ts 和 useProgressSocket.ts
// 路径: frontend/src/api/useProgressSocket.ts (不是 composables/)
// import 路径:
import { onTokenDelta } from '@/api/useProgressSocket'

// client.ts 加 DTO（保持项目 DTO 风格）
export interface TokenDelta {
  context: 'analysis' | 'summary_phase_1' | 'summary_phase_2' | 'summary_phase_3' | 'summary_phase_4'
  session_id: string
  delta: {
    output_tokens: number
    rate_tokens_per_sec: number
    elapsed_sec: number
    eta_sec?: number
    batch_idx?: number
    block_idx?: number
  }
  timestamp: number
}

// 消息信封对齐 {type, payload} 模式（参考现有 message handler）
// useProgressSocket.ts 中消息处理：
} else if (msg.type === 'token_delta') {
  const payload = msg.payload as TokenDelta
  tokenDeltaSubscribers.forEach(cb => cb(payload))
}

// ProgressHub 发送时（Task 1.4）：
await self.broadcast({
  "type": "token_delta",
    "payload": {
      "context": context,
      "session_id": session_id,
      "delta": delta,
      "timestamp": time.time(),
    },
})
```

```vue
<!-- 应用到 Task 3.4: LiveTokenCounter.vue sparkline 用纯 SVG 不用 d3 -->
<template>
  <div class="flex flex-col gap-2 p-3 rounded bg-white/5">
    <div class="flex items-baseline gap-3">
      <span class="text-2xl font-mono tabular-nums">{{ Math.round(displayOutput).toLocaleString() }}</span>
      <span class="text-sm text-gray-500">tokens</span>
      <span :class="['ml-auto text-sm', rateColor]">{{ rate.toFixed(0) }} tok/s</span>
    </div>
    <!-- 30 点 sparkline：纯 SVG polyline，不引入 d3 -->
    <svg :width="240" :height="32" class="text-gray-400">
      <polyline
        :points="sparklinePoints"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linejoin="round"
      />
    </svg>
    <div class="text-xs text-gray-500">已用 {{ elapsed.toFixed(1) }}s</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { onTokenDelta, type TokenDelta } from '@/api/useProgressSocket'

const props = defineProps<{ context: string, bookId: string }>()

const targetOutput = ref(0)
const displayOutput = ref(0)
const rate = ref(0)
const elapsed = ref(0)
const rateHistory = ref<number[]>([])
let raf: number | null = null
let lastSample = 0

const sparklinePoints = computed(() => {
  const w = 240, h = 32, n = 30
  const max = Math.max(...rateHistory.value, 1)
  return rateHistory.value.map((v, i) => {
    const x = (i / (n - 1)) * w
    const y = h - (v / max) * h
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
})

const rateColor = computed(() => {
  if (rate.value > 300) return 'text-green-500'
  if (rate.value > 100) return 'text-yellow-500'
  return 'text-red-500'
})

function animate() {
  const diff = targetOutput.value - displayOutput.value
  displayOutput.value += diff * 0.18
  if (Math.abs(diff) < 0.5) displayOutput.value = targetOutput.value
  raf = requestAnimationFrame(animate)
}

const unsubscribe = onTokenDelta((delta) => {
  if (delta.context !== props.context || delta.session_id !== props.bookId) return
  targetOutput.value = delta.delta.output_tokens
  rate.value = delta.delta.rate_tokens_per_sec
  elapsed.value = delta.delta.elapsed_sec
  const now = Date.now()
  if (now - lastSample > 1000) {
    rateHistory.value.push(rate.value)
    if (rateHistory.value.length > 30) rateHistory.value.shift()
    lastSample = now
  }
})

onMounted(() => { raf = requestAnimationFrame(animate) })
onUnmounted(() => { 
  if (raf) cancelAnimationFrame(raf)
  unsubscribe()
})
</script>
```

**测试文件路径**：用项目惯例同目录 `.test.ts`，**不是** `__tests__/*.spec.ts`：
```
frontend/src/components/LiveTokenCounter.test.ts
```

**应用**：
- Task 3.3 import 路径用 `@/api/useProgressSocket`
- Task 3.3 消息信封 `{type, payload}` 模式
- Task 3.4 sparkline 用纯 SVG polyline
- Task 3.4 测试文件 `LiveTokenCounter.test.ts`（同目录）
- Phase 4 Task 4.1 测试路径全改成同目录 `.test.ts`

---

### P1-7: partial 落盘缺读端（增量补全逻辑）

**问题**：Decision 3 和 Task 2.1 只实现了 `_save_partial_reconciliation` 落盘，"下次启动检测到则增量补全"的**读取/合并逻辑没有任何对应 task**。另外 `_call_llm_reconciliation(prompt)` 实际没有 `batch_idx` 参数，加参数连调用点也要改。

**核对证据**：`final_summary.py:645` `async def _call_llm_reconciliation(self, prompt: str)` 无 batch_idx。

**修订**：

```python
# 1) _call_llm_reconciliation 加 batch_idx 参数
# 应用到 Task 2.1
async def _call_llm_reconciliation(self, prompt: str, batch_idx: Optional[int] = None) -> Optional[dict]:
    """第二次调用：产出伏笔 reconciliation JSON（流式版）"""
    messages = [
        {"role": "system", "content": RECONCILIATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    await self._acquire_llm_slot()
    started_at = time.monotonic()
    
    async def on_progress(chunk: StreamChunk):
        if chunk.type in ("content", "usage"):
            elapsed = time.monotonic() - started_at
            await progress_hub.broadcast_token_delta(
                context="summary_phase_1",
                session_id=self.book_id,
                delta={
                    "batch_idx": batch_idx,
                    "output_tokens": chunk.estimated_total_tokens or 0,  # P0-4 累计
                    "rate_tokens_per_sec": (chunk.estimated_total_tokens or 0) / max(elapsed, 0.1),
                    "elapsed_sec": elapsed,
                },
            )
    
    try:
        result = await self._llm.chat_stream_with_retry(
            messages,
            max_tokens=self.config.api.max_tokens,
            validate_response=validate_reconciliation_json,
            retry_messages_builder=build_reconciliation_retry_messages,
            on_progress=on_progress,
        )
    finally:
        self._release_llm_slot()
    
    self._record_tokens("reconciliation", (result.prompt_tokens, result.completion_tokens))
    
    if not result.success:
        logger.warning(f"伏笔 reconciliation 调用失败: {result.error}")
        # 流式中断时尝试用 partial content 解析
        if result.content:
            partial = extract_foreshadow_json(result.content)
            if partial and partial.get("reconciliation"):
                logger.info(f"残缺 JSON 解析成功: {len(partial['reconciliation'])} 条（partial）")
                if batch_idx is not None:
                    self._save_partial_reconciliation(batch_idx, partial)
                return partial
        return None
    
    return extract_foreshadow_json(result.content)


# 2) 新增 _save_partial_reconciliation 落盘（plan 已有，仅补充）
def _save_partial_reconciliation(self, batch_idx: int, data: dict) -> None:
    """残缺 reconciliation 落盘：下次启动时检测到则增量补全（不覆盖已有 recon_N.json）"""
    checkpoint_dir = self._checkpoint_dir()
    partial_path = checkpoint_dir / f"recon_{batch_idx}_partial.json"
    data["_partial"] = True
    data["_saved_at"] = datetime.now().isoformat()
    safe_save_json(partial_path, data)
    logger.info(f"残缺 reconciliation 已落盘: {partial_path}")


# 3) 新增 _load_partial_reconciliations 读端（plan 缺此 task）
# 应用到 Phase 2 Task 2.1（新增 Task 2.1.3）
def _load_partial_reconciliations(self) -> Dict[int, dict]:
    """启动时检测 partial 落盘：返回 {batch_idx: partial_data}；调用方负责合并"""
    checkpoint_dir = self._checkpoint_dir()
    if not checkpoint_dir.exists():
        return {}
    partials = {}
    for f in checkpoint_dir.glob("recon_*_partial.json"):
        try:
            data = safe_load_json(f)
            batch_idx = int(f.stem.split("_")[1])
            if data.get("_partial"):
                partials[batch_idx] = data
                logger.info(f"检测到残缺 reconciliation: batch={batch_idx}, "
                            f"reconciled={len(data.get('reconciliation', []))} 条")
        except (ValueError, OSError) as e:
            logger.warning(f"读取 partial 失败 {f}: {e}")
    return partials


# 4) 在 run() 启动处检测 partial 并提示用户
# 应用到 Phase 2 run() 启动（替换原 plan 的"启动期检测"步骤）
async def _check_partial_recoveries(self) -> Dict[int, dict]:
    """扫描 checkpoint 目录的 partial 文件，弹窗/日志告知用户"""
    partials = self._load_partial_reconciliations()
    if partials:
        total_items = sum(len(p.get("reconciliation", [])) for p in partials.values())
        logger.warning(f"⚠️ 检测到 {len(partials)} 个残缺 reconciliation（{total_items} 伏笔），"
                       f"将尝试增量补全；如重跑成功会覆盖 partial 文件")
        # WebSocket 广播提示前端展示
        await progress_hub.broadcast({
            "type": "state_change",
            "payload": {
                "state": "partial_recovery",
                "count": len(partials),
                "total_items": total_items,
            }
        })
    return partials
```

**应用**：
- Task 2.1 改 `_call_llm_reconciliation` 加 `batch_idx` 参数（所有调用点同步加）
- Task 2.1 加 `_save_partial_reconciliation` + `_load_partial_reconciliations` + `_check_partial_recoveries`
- Phase 2 加测试覆盖 partial 读取

---

### P1-8: 并发场景 token_delta 混流

**问题**：分析阶段多 block 并发，全都广播到 `context="analysis"` 同一 channel；ThrottledBroadcaster"合并最新值"会把不同 block 的数字互相覆盖，前端计数器来回跳。summary 阶段 phase_1 summary + reconciliation 并发共用 context 也一样。

**核对证据**：plan Task 1.4 的 `ThrottledBroadcaster._flush` 用 `self._latest[channel]` 合并最新值，单 channel 会丢早期 block 的 token。

**修订**：channel key 改为 `(context, session_id, batch_idx/block_idx)` 三元组，**不做合并**，每个 key 独立 throttler 桶。

```python
# 应用到 Task 1.4 ThrottledBroadcaster
class ThrottledBroadcaster:
    """按 channel key 独立节流（不合并不同 key 的最新值）；
    适合流式 token 增量广播 + 多 block 并发场景。"""
    MIN_INTERVAL = 0.15  # 150ms

    def __init__(self):
        self._last_emit: Dict[str, float] = {}
        self._pending_tasks: Dict[str, asyncio.Task] = {}

    async def emit(self, channel_key: str, data: dict) -> None:
        # channel_key 推荐格式: f"{context}:{session_id}:{unit_idx}"
        # 例: "analysis:book_001:block_42" 或 "summary_phase_1:book_001:batch_2"
        now = asyncio.get_running_loop().time()  # 修正：get_event_loop 已弃用
        elapsed = now - self._last_emit.get(channel_key, 0)
        if elapsed >= self.MIN_INTERVAL:
            await self._hub.broadcast(data)  # 修正：注入 hub 不调 self.broadcast
            self._last_emit[channel_key] = now
        elif channel_key not in self._pending_tasks:
            self._pending_tasks[channel_key] = asyncio.create_task(self._delayed_emit(channel_key, data))

    async def _delayed_emit(self, channel_key: str, data: dict):
        await asyncio.sleep(self.MIN_INTERVAL)
        await self._hub.broadcast(data)
        self._last_emit[channel_key] = asyncio.get_running_loop().time()
        self._pending_tasks.pop(channel_key, None)


# 应用到 ProgressHub
class ProgressHub:
    def __init__(self):
        ...
        self._token_throttler = ThrottledBroadcaster()
        # ThrottledBroadcaster 构造函数改为接受 hub 引用
        # 或者用闭包注入
        self._token_throttler._hub = self  # 简单注入

    async def broadcast_token_delta(self, context, session_id, unit_idx, delta):
        """unit_idx 来自 block_idx 或 batch_idx；多并发不互相覆盖"""
        channel_key = f"{context}:{session_id}:{unit_idx}"
        msg = {
            "type": "token_delta",
            "payload": {  # P1-6 消息信封对齐
                "context": context,
                "session_id": session_id,
                "unit_idx": unit_idx,
                "delta": delta,
                "timestamp": time.time(),
            }
        }
        await self._token_throttler.emit(channel_key, msg)
```

**应用**：
- Task 1.4 ThrottledBroadcaster 改 channel key 三元组 + 不合并
- Task 1.4 用 `get_running_loop()` 替代 `get_event_loop()`（已弃用）
- Task 1.4 ThrottledBroadcaster 注入 hub 引用（避免 self.broadcast 错误）
- 所有 `_call_llm_*` 和 `_analyze_one_block` 的 `on_progress` 都传 `unit_idx`
- 前端订阅逻辑：按 `(context, session_id, unit_idx)` 独立展示，不再合并

---

### 小问题修订（7 个）

#### 小-1: 统计口径回归（`_total_tokens` 等不更新）

**问题**：流式路径不调 `self._total_tokens += ...` / `self._cached_tokens += ...` / `self._attempts += 1` / `self._failed_tokens += ...`，导致 StatsPage 漏掉所有流式调用。

**修订**：在 P0-2 修订后的 `chat_stream_with_retry` 内部按 chat_with_retry 同样的位置累加（已有 `_attempts` / `_total_tokens` / `_cached_tokens` / `_failed_tokens` / `last_consumed_tokens` 累加代码块）。

**应用**：P0-2 修订已含此修复。

#### 小-2: 空响应误判成功

**问题**：流式 chunk 全空时 `success=True, content=""` —— 老代码有 moderation 空内容检测（`llm_client.py:668-673`），流式版需加。

**修订**：在 chat_stream_with_retry 成功分支加空内容检查：

```python
# 应用到 P0-2 修订的 chat_stream_with_retry 成功分支
# 在返回 success=True 之前
if not attempt_content.strip():
    error_msg = "API返回空内容（可能触发内容过滤）"
    if last_usage and is_moderation_message(str(last_usage)):
        error_msg = mark_moderation(error_msg)
    logger.warning(error_msg)
    # 算失败，让重试链处理
    last_error = error_msg
    _get_failure_logger().record_failure(...)
    if attempt < self.config.temperature_max_retries - 1:
        await self._sleep(3 * (attempt + 1))
    continue
```

**应用**：P0-2 修订已含此检查。

#### 小-3: `stream_options` 注释错误

**问题**：plan 注释说"强制每个 chunk 带 usage"，实际 OpenAI 流式**只在最后一个 chunk 带 usage（且 choices 为空）**。

**修订**：plan 注释改为：
```python
# OpenAI stream_options={"include_usage": True}：在最后一个 chunk 带 usage 字段（choices 为空）
# 中间 chunk 只有 content/reasoning，没有 usage
```

**应用**：Task 1.2 chat_stream 注释修正。

#### 小-4: 缓解措施不完整

**问题**：plan 只说"部分聚合网关不认 stream_options 兜底估算"，没说怎么处理。

**修订**：加降级重试一次不带该参数：

```python
# 应用到 Task 1.2 chat_stream
# 先尝试带 stream_options
try:
    stream = await self.client.chat.completions.create(..., stream_options={"include_usage": True})
except (TypeError, APIError) as e:
    if "stream_options" in str(e):
        logger.warning(f"该端点不支持 stream_options，降级不带该参数: {e}")
        stream = await self.client.chat.completions.create(..., stream=False)  # 退化为非流式
        # 走非流式 chat() 路径
    else:
        raise
```

**应用**：Task 1.2 chat_stream 异常处理加降级分支。

#### 小-5: `taskkill /IM python.exe /F` 误杀

**问题**：会杀掉机器上**所有** python 进程（包括其他项目的）。

**修订**：用 PID 或窗口名匹配：

```powershell
# 推荐：通过 app.lock 获取 PID
$lock = Get-Content "F:\AI\01_项目\小说分析器\app.lock" -Raw | ConvertFrom-Json
$pid_to_kill = $lock.pid
Stop-Process -Id $pid_to_kill -Force  # 桌面端进程

# 或通过窗口标题匹配
Get-Process | Where-Object { $_.MainWindowTitle -like "*小说智能分析器*" } | Stop-Process -Force
```

**应用**：Task 2.2 修订重启脚本。

#### 小-6: 验收数字"255+18=273"过时

**问题**：实际 pytest 节点数可能不是 255（最新跑的是 255 passed，但 collect-only 输出是 test 节点数，不是用例数）。

**修订**：验收标准改为：
```
- 现有 pytest 用例 0 回归（不限具体数字）
- 后端新增 ≥12 用例
- 前端新增 ≥6 用例
```

**应用**：Acceptance Criteria 第 6 条。

#### 小-7: 回滚开关必做（见 Decisions 4）

见顶部 Decisions 第 4 条。**Phase 1 就该加** `streaming_enabled` 开关，出问题能秒切回老链路。

**应用到 Phase 1 新增 Task 1.0**：
```python
# Task 1.0: 加 streaming_enabled 配置开关（Phase 1 落地）
# Files: backend/config/settings.py + constants.py + config.json

# settings.py: 在 APIConfig 加字段
@dataclass
class APIConfig:
    ...
    streaming_enabled: bool = True  # 默认 true，false 时回退到旧 chat_with_retry 路径

# AnalysisConfig 同样：
@dataclass
class AnalysisConfig:
    ...
    streaming_enabled: bool = True  # 分析阶段流式开关
    summary_streaming_enabled: bool = True  # 总结阶段流式开关

# 调用方逻辑：
async def _call_llm_with_config(self, ...):
    if self.config.api.streaming_enabled:
        return await self._llm.chat_stream_with_retry(...)
    else:
        return await self._llm.chat_with_retry(...)  # 旧 API 兜底
```

**应用**：
- Phase 1 加 Task 1.0（落地 streaming_enabled 开关）
- 所有 `_call_llm_*` 改成走 `_call_llm_with_config` 包装

---

### 修订落地清单（实施时按此 checklist 改主 plan）

| # | 来源 | 修订 | 改主 plan 的 Task |
|---|---|---|---|
| R1 | Decisions 1-6 | 已批准 | §Open Questions 整段删除，改 Decisions 表格 |
| R2 | P0-1 | chat_stream + chat_stream_with_retry 加 stop 检查 | Task 1.2 / 1.3 |
| R3 | P0-2 | chat_stream_with_retry 完整重写对齐 chat_with_retry | Task 1.3 整段 |
| R4 | P0-3 | `_call_validate_response` 兼容同步/异步 | Task 1.3 |
| R5 | P0-4 | on_progress 累计值 | Task 1.3 / 2.1 |
| R6 | P1-5 | 改 analyzer.py 不改 pipeline | Task 3.2 |
| R7 | P1-5 | 改 style_analyzer.py | Task 3.1 加子任务 |
| R8 | P1-6 | useProgressSocket 路径 + 消息信封 + sparkline 纯 SVG + 测试 `.test.ts` | Task 3.3 / 3.4 / 4.1 |
| R9 | P1-7 | `_call_llm_reconciliation` 加 batch_idx + partial 读端 | Task 2.1 |
| R10 | P1-8 | ThrottledBroadcaster 不合并 + channel key 三元组 | Task 1.4 |
| R11 | 小-1 | 统计累加 | P0-2 已含 |
| R12 | 小-2 | 空响应检测 | P0-2 已含 |
| R13 | 小-3 | stream_options 注释 | Task 1.2 |
| R14 | 小-4 | stream_options 降级分支 | Task 1.2 |
| R15 | 小-5 | restart 脚本用 PID | Task 2.2 |
| R16 | 小-6 | 验收数字改"0 回归" | Acceptance Criteria |
| R17 | 小-7 | streaming_enabled 开关必做 | 新 Task 1.0 |

