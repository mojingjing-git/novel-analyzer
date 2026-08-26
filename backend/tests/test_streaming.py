"""
H16 (2026-08-26) 流式 LLM 调用测试

覆盖：
- LLMClient._parse_openai_stream_event / _parse_anthropic_stream_event 单元解析
- LLMClient.chat_stream 异步生成器（stop、降级、错误处理）
- LLMClient.chat_stream_with_retry 完整重试链（温度退火 / 指数退避 / 429 / 验证 / stop / 累计值）
- LLMClient._call_validate_response 同步/异步双兼容
"""
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config.settings import APIConfig
from backend.core.llm_client import (
    LLMClient, StreamChunk, StreamResult,
    detect_provider,
)


# ============================ fixtures ============================

def make_config(**kwargs):
    defaults = dict(
        base_url="https://api.test.com",
        api_key="test-key",
        model="test-model",
        max_tokens=1000,
        timeout=10,
        max_retries=3,
        json_mode="default",
        temperature=0.5,
        temperature_step=0.1,
        temperature_max_retries=2,
        backoff_max_retries=1,
        thinking_mode={},
        provider="openai",
        streaming_enabled=True,
    )
    defaults.update(kwargs)
    return APIConfig(**defaults)


def make_event(choices=None, usage=None):
    """构造 OpenAI 流式事件 mock"""
    ev = MagicMock()
    ev.choices = choices or []
    if usage is not None:
        ev.usage = usage
    else:
        ev.usage = None
    return ev


def make_delta(content="", reasoning=""):
    """构造 OpenAI choice delta"""
    d = MagicMock()
    d.content = content
    d.reasoning_content = reasoning
    return d


def make_usage_event(prompt=10, completion=20, cached=0):
    """构造 OpenAI usage-only chunk（最后一个）"""
    u = MagicMock()
    u.prompt_tokens = prompt
    u.completion_tokens = completion
    ptd = MagicMock()
    ptd.cached_tokens = cached
    u.prompt_tokens_details = ptd
    u.prompt_cache_hit_tokens = 0
    return make_event(usage=u)


# ============================ _parse_*_stream_event ============================

class TestParseOpenAIStreamEvent:
    def test_content_chunk(self):
        client = LLMClient(make_config())
        delta = make_delta(content="hello")
        choice = MagicMock()
        choice.delta = delta
        ev = make_event(choices=[choice])
        chunk = client._parse_openai_stream_event(ev)
        assert chunk is not None
        assert chunk.type == "content"
        assert chunk.text == "hello"

    def test_reasoning_chunk(self):
        client = LLMClient(make_config())
        delta = make_delta(reasoning="thinking...")
        choice = MagicMock()
        choice.delta = delta
        ev = make_event(choices=[choice])
        chunk = client._parse_openai_stream_event(ev)
        assert chunk is not None
        assert chunk.type == "reasoning"
        assert chunk.reasoning_text == "thinking..."

    def test_usage_only_chunk(self):
        client = LLMClient(make_config())
        ev = make_usage_event(prompt=100, completion=50, cached=10)
        chunk = client._parse_openai_stream_event(ev)
        assert chunk is not None
        assert chunk.type == "usage"
        assert chunk.usage_prompt_tokens == 100
        assert chunk.usage_completion_tokens == 50
        assert chunk.usage_cached_tokens == 10
        assert chunk.is_final is True

    def test_empty_delta_returns_none(self):
        client = LLMClient(make_config())
        delta = make_delta(content="", reasoning="")
        choice = MagicMock()
        choice.delta = delta
        ev = make_event(choices=[choice])
        assert client._parse_openai_stream_event(ev) is None

    def test_no_choices_no_usage_returns_none(self):
        client = LLMClient(make_config())
        ev = make_event()
        ev.usage = None
        assert client._parse_openai_stream_event(ev) is None


# ============================ chat_stream 流式基础 ============================

class TestChatStream:
    async def test_openai_streaming_yields_chunks(self):
        """OpenAI 流式：每 chunk 立即 yield，content 累积"""
        client = LLMClient(make_config())

        # Mock OpenAI stream
        events = [
            make_event(choices=[MagicMock(delta=make_delta(content="你"))]),
            make_event(choices=[MagicMock(delta=make_delta(content="好"))]),
            make_event(choices=[MagicMock(delta=make_delta(reasoning="思考中"))]),
            make_usage_event(prompt=5, completion=3),
        ]

        async def mock_stream():
            for ev in events:
                yield ev

        with patch.object(client, "_build_anthropic_payload", return_value={}):
            client.client = MagicMock()
            client.client.chat.completions.create = AsyncMock(return_value=mock_stream())
            client._is_429 = lambda s: False
            client._retry_after_seconds = lambda s: None
            client._strip_thinking = lambda s: s

            chunks = []
            async for c in client.chat_stream([{"role": "user", "content": "hi"}]):
                chunks.append(c)

        content_chunks = [c for c in chunks if c.type == "content"]
        reasoning_chunks = [c for c in chunks if c.type == "reasoning"]
        usage_chunks = [c for c in chunks if c.type == "usage"]

        assert len(content_chunks) == 2
        assert content_chunks[0].text == "你"
        assert content_chunks[1].text == "好"
        assert len(reasoning_chunks) == 1
        assert reasoning_chunks[0].reasoning_text == "思考中"
        assert len(usage_chunks) == 1
        assert usage_chunks[0].usage_completion_tokens == 3

    async def test_stop_requested_closes_stream(self):
        """_stop_requested 触发后，stream 立即关闭"""
        client = LLMClient(make_config())
        client._stop_requested = True  # 模拟 stop

        events = [make_event(choices=[MagicMock(delta=make_delta(content="x"))])]

        async def mock_stream():
            for ev in events:
                yield ev

        with patch.object(client, "_build_anthropic_payload", return_value={}):
            client.client = MagicMock()
            client.client.chat.completions.create = AsyncMock(return_value=mock_stream())

            chunks = []
            async for c in client.chat_stream([{"role": "user", "content": "hi"}]):
                chunks.append(c)

        # stop 已置位，第 1 个 chunk 前就关闭，应该 0 yield
        assert len(chunks) == 0

    async def test_stream_options_fallback_on_typeerror(self):
        """stream_options 不被支持时降级（不带该参数）"""
        client = LLMClient(make_config())

        events = [make_event(choices=[MagicMock(delta=make_delta(content="ok"))])]

        async def mock_stream():
            for ev in events:
                yield ev

        call_count = [0]

        async def mock_create(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TypeError("stream_options not supported")
            return mock_stream()

        with patch.object(client, "_build_anthropic_payload", return_value={}):
            client.client = MagicMock()
            client.client.chat.completions.create = mock_create

            chunks = []
            async for c in client.chat_stream([{"role": "user", "content": "hi"}]):
                chunks.append(c)

        # 第一次带 stream_options 失败，第二次不带
        assert call_count[0] == 2
        assert len(chunks) == 1


# ============================ _call_validate_response 兼容 ============================

class TestCallValidateResponse:
    async def test_sync_validator(self):
        client = LLMClient(make_config())

        def sync_validator(content):
            if "good" in content:
                return (True, "")
            return (False, "missing 'good'")

        is_valid, err = await client._call_validate_response(sync_validator, "this is good content")
        assert is_valid is True
        assert err == ""

    async def test_async_validator(self):
        client = LLMClient(make_config())

        async def async_validator(content):
            if "good" in content:
                return (True, "")
            return (False, "missing 'good'")

        is_valid, err = await client._call_validate_response(async_validator, "this is good content")
        assert is_valid is True
        assert err == ""

    async def test_sync_validator_rejects_bad(self):
        client = LLMClient(make_config())

        def sync_validator(content):
            if "good" in content:
                return (True, "")
            return (False, "missing 'good'")

        is_valid, err = await client._call_validate_response(sync_validator, "this is bad content")
        assert is_valid is False
        assert "missing 'good'" in err

    async def test_sync_validator_timeout(self):
        client = LLMClient(make_config())

        def slow_validator(content):
            time.sleep(35)
            return (True, "")

        is_valid, err = await client._call_validate_response(slow_validator, "x", timeout=0.1)
        assert is_valid is False
        assert "验证超时" in err

    async def test_sync_validator_exception(self):
        client = LLMClient(make_config())

        def bad_validator(content):
            raise ValueError("oops")

        is_valid, err = await client._call_validate_response(bad_validator, "x")
        assert is_valid is False
        assert "验证异常" in err
        assert "oops" in err


# ============================ chat_stream_with_retry 重试链 ============================

class TestChatStreamWithRetry:
    async def test_success_on_first_try(self):
        """首次调用成功，on_progress 收到累计值"""
        client = LLMClient(make_config())

        async def mock_stream_chunks():
            yield StreamChunk(type="content", text="hello")
            yield StreamChunk(type="content", text=" world")
            yield StreamChunk(type="usage", usage_prompt_tokens=5,
                              usage_completion_tokens=2, usage_cached_tokens=0, is_final=True)

        async def mock_chat_stream(*args, **kwargs):
            async for c in mock_stream_chunks():
                yield c

        progress_values = []

        async def on_progress(chunk: StreamChunk):
            progress_values.append(chunk.estimated_total_tokens)

        client.chat_stream = mock_chat_stream
        result = await client.chat_stream_with_retry(
            [{"role": "user", "content": "hi"}],
            on_progress=on_progress,
        )

        assert result.success is True
        assert result.content == "hello world"
        assert result.completion_tokens == 2
        assert result.prompt_tokens == 5
        # on_progress 应该收到累计值（len/4）：5//4=1, 6//4=1（helloworld 是 10 char，10//4=2）
        # 实际是 "hello"(5) + " world"(6) = 11 char
        assert progress_values[0] == 5 // 4
        assert progress_values[1] == 11 // 4

    async def test_validation_failure_triggers_retry_with_hint(self):
        """验证失败 → 注入 retry_messages_builder 的 hint → 重试成功"""
        client = LLMClient(make_config(temperature_max_retries=2, backoff_max_retries=0))

        call_count = [0]

        async def mock_stream_chunks_attempt1():
            yield StreamChunk(type="content", text='{"bad": "json"}')

        async def mock_stream_chunks_attempt2():
            yield StreamChunk(type="content", text='{"good": "json"}')
            yield StreamChunk(type="usage", usage_prompt_tokens=1,
                              usage_completion_tokens=1, is_final=True)

        async def mock_chat_stream(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                async for c in mock_stream_chunks_attempt1():
                    yield c
            else:
                async for c in mock_stream_chunks_attempt2():
                    yield c

        received_messages = []

        def validator(content):
            if "good" in content:
                return True, ""
            return False, "missing 'good' field"

        def retry_builder(validation_error, current_messages):
            # 验证 retry_messages_builder 被调用且 messages 被修改
            new_msgs = [dict(m) for m in current_messages]
            for m in reversed(new_msgs):
                if m.get("role") == "user":
                    m["content"] = m["content"] + f"\n[hint] {validation_error}"
                    break
            received_messages.append(new_msgs)
            return new_msgs

        client.chat_stream = mock_chat_stream
        result = await client.chat_stream_with_retry(
            [{"role": "user", "content": "hi"}],
            validate_response=validator,
            retry_messages_builder=retry_builder,
        )

        assert result.success is True
        assert result.content == '{"good": "json"}'
        assert call_count[0] == 2
        # retry_messages_builder 至少被调用 1 次
        assert len(received_messages) >= 1
        # 第二次调用时 messages 应包含 hint
        last_msgs = received_messages[-1]
        assert any("[hint]" in str(m.get("content", "")) for m in last_msgs)

    async def test_stop_during_retry_terminates_immediately(self):
        """stop 信号立即终止重试链"""
        client = LLMClient(make_config(temperature_max_retries=5))

        async def mock_chat_stream_always_error(*args, **kwargs):
            yield StreamChunk(type="error", error="API error")
            return  # generator end

        # 第一次 attempt 完成后设置 stop
        call_count = [0]

        async def mock_chat_stream_with_stop(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                client._stop_requested = True
            yield StreamChunk(type="error", error="API error")
            return

        client.chat_stream = mock_chat_stream_with_stop
        result = await client.chat_stream_with_retry([{"role": "user", "content": "hi"}])

        # 验证 stop 后立即退出，不继续重试
        assert result.success is False
        assert "用户请求停止" in result.error
        # 调用次数：temperature_max_retries=5 但 stop 在第 2 次触发，所以 ≤ 2 次
        assert call_count[0] <= 2

    async def test_empty_response_treated_as_failure(self):
        """空内容（moderation 嗅探）按失败处理"""
        client = LLMClient(make_config(temperature_max_retries=2, backoff_max_retries=0))

        call_count = [0]

        async def mock_chat_stream(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                yield StreamChunk(type="content", text="")  # 空内容
                yield StreamChunk(type="content", text="   ")  # 空白
            else:
                yield StreamChunk(type="content", text="non-empty")
                yield StreamChunk(type="usage", usage_prompt_tokens=1,
                                  usage_completion_tokens=1, is_final=True)

        client.chat_stream = mock_chat_stream
        result = await client.chat_stream_with_retry([{"role": "user", "content": "hi"}])

        assert result.success is True
        assert result.content == "non-empty"
        assert call_count[0] == 2

    async def test_429_triggers_retry_after_backoff(self):
        """429 错误：等待 Retry-After + 进入指数退避多轮"""
        client = LLMClient(make_config(temperature_max_retries=1, backoff_max_retries=2))

        call_count = [0]
        sleep_calls = []

        async def mock_chat_stream(*args, **kwargs):
            call_count[0] += 1
            yield StreamChunk(type="error", error="API错误 (HTTP 429): rate limited [Retry-After:1]")

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        client.chat_stream = mock_chat_stream
        client._sleep = mock_sleep
        result = await client.chat_stream_with_retry([{"role": "user", "content": "hi"}])

        assert result.success is False
        # 1 次温度退火 + 至少 1 次指数退避（429 时 backoff_rounds = max(1, 3) = 3）
        # 至少 2 次 sleep
        assert len(sleep_calls) >= 2

    async def test_last_error_resets_between_attempts(self):
        """P0-2 修复：last_error 在每 attempt 失败时被显式覆盖，下一 attempt 不会因
        'last_error != "未知错误"' 恒真而丢弃成功结果"""
        client = LLMClient(make_config(temperature_max_retries=3, backoff_max_retries=0))

        call_count = [0]

        async def mock_chat_stream(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # 第 1 次：流式 chunk 有 error
                yield StreamChunk(type="error", error="transient error")
            else:
                # 第 2 次：成功
                yield StreamChunk(type="content", text="success")
                yield StreamChunk(type="usage", usage_prompt_tokens=1,
                                  usage_completion_tokens=1, is_final=True)

        client.chat_stream = mock_chat_stream
        client._sleep = AsyncMock()  # 跳过 sleep
        result = await client.chat_stream_with_retry([{"role": "user", "content": "hi"}])

        # 必须成功（不是被 last_error 残留导致丢弃）
        assert result.success is True
        assert result.content == "success"
        assert call_count[0] == 2

    async def test_partial_content_preserved_on_total_failure(self):
        """P1-a (2026-08-26) 修复：流式中断后 partial content 必须保留到 final_content
        失败路径（认证/审核/退避耗尽）返回的 result.content 是下游 recon_partial
        容错链的唯一输入；如果 _run_stream_attempt 拼好的内容没回写到 final_content，
        下游拿到的永远是空串，整条残缺 JSON 兜底链就断了。

        场景：1 次温度退火 + 0 次退避，attempt 内部流发出 2 个 content chunk
        后被 error chunk 中断，触发重试但 backoff 关闭 → 整体失败。"""
        client = LLMClient(make_config(temperature_max_retries=1, backoff_max_retries=0))

        partial_text_1 = '{"foreshadows":[{"clue":"残缺片段 A'

        async def mock_chat_stream(*args, **kwargs):
            # 流式 chunk：先发部分内容，然后 error 中断
            yield StreamChunk(type="content", text=partial_text_1)
            yield StreamChunk(type="error", error="stream truncated mid-response")

        client.chat_stream = mock_chat_stream
        client._sleep = AsyncMock()  # 跳过 sleep
        result = await client.chat_stream_with_retry([{"role": "user", "content": "hi"}])

        # 整体失败（最后一条 error chunk）
        assert result.success is False
        # P1-a 核心断言：partial content 必须保留
        # 修复前 final_content 永远是 ""，修复后是最后 attempt 的拼接结果
        assert result.content == partial_text_1
        assert result.content != ""
        print("✅ test_partial_content_preserved_on_total_failure passed")


# ============================ DetectProvider ============================

class TestDetectProvider:
    def test_explicit_provider_openai(self):
        # 不传 provider，走 auto 分支用其他特征判定
        cfg = make_config(provider="openai")
        assert detect_provider(cfg) == "openai"

    def test_anthropic_key(self):
        cfg = make_config(api_key="sk-ant-xxxx")
        cfg.provider = "auto"  # 强制走 auto 分支
        assert detect_provider(cfg) == "anthropic"

    def test_claude_model(self):
        cfg = make_config(model="claude-3-opus")
        cfg.provider = "auto"  # 强制走 auto 分支
        assert detect_provider(cfg) == "anthropic"


# ============================ 入口 ============================

if __name__ == "__main__":
    # 简易手动跑（pytest 异步需要 asyncio_mode）
    print("Run with: python -m pytest backend/tests/test_streaming.py -v")
