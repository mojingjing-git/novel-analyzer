"""
测试 LLMClient 温度退火 + 指数退避重试链（使用 mock）
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config.settings import APIConfig
from backend.core.llm_client import LLMClient


def make_config(**kwargs):
    defaults = dict(
        base_url="https://api.test.com",
        api_key="test-key",
        model="test-model",
        max_tokens=1000,
        timeout=10,
        max_retries=3,
        json_mode="default",
        temperature=0.7,
        temperature_step=0.1,
        temperature_max_retries=3,
        backoff_max_retries=2,
        thinking_mode={},
    )
    defaults.update(kwargs)
    return APIConfig(**defaults)


async def test_success_on_first_try():
    """首次调用成功"""
    config = make_config()
    client = LLMClient(config)
    # Mock chat method
    client.chat = AsyncMock(return_value=(True, '{"result":"ok"}', '', (10, 20)))
    success, content, error, tokens = await client.chat_with_retry([{"role": "user", "content": "hi"}])
    assert success
    assert content == '{"result":"ok"}'
    assert tokens == (10, 20)
    assert client.chat.call_count == 1
    print("✅ test_success_on_first_try passed")


async def test_temperature_annealing():
    """温度退火：第一次失败，第二次成功"""
    config = make_config(temperature=0.7, temperature_step=0.1, temperature_max_retries=3, backoff_max_retries=0)
    client = LLMClient(config)
    call_temps = []

    async def mock_chat(messages, temperature=0.1, max_tokens=None):
        call_temps.append(temperature)
        if len(call_temps) == 1:
            return (False, '', 'API error', (0, 0))
        return (True, '{"result":"ok"}', '', (10, 20))

    client.chat = mock_chat
    success, content, error, tokens = await client.chat_with_retry([{"role": "user", "content": "hi"}])
    assert success
    assert len(call_temps) == 2
    # First temp: 0.7, second: 0.6
    assert call_temps[0] == 0.7
    assert call_temps[1] == pytest_approx(0.6)
    print("✅ test_temperature_annealing passed")


async def test_authentication_error_stops_immediately():
    """认证错误立即停止"""
    config = make_config(temperature_max_retries=3)
    client = LLMClient(config)
    client.chat = AsyncMock(return_value=(False, '', '认证失败，请检查API Key', (0, 0)))
    success, content, error, tokens = await client.chat_with_retry([{"role": "user", "content": "hi"}])
    assert not success
    assert "认证" in error
    # Should only call once (auth error stops immediately)
    assert client.chat.call_count == 1
    print("✅ test_authentication_error_stops_immediately passed")


async def test_stop_requested():
    """停止请求终止重试链"""
    config = make_config(temperature_max_retries=5)
    client = LLMClient(config)
    client.chat = AsyncMock(return_value=(False, '', 'API error', (0, 0)))
    client.request_stop()
    success, content, error, tokens = await client.chat_with_retry([{"role": "user", "content": "hi"}])
    assert not success
    assert "停止" in error
    print("✅ test_stop_requested passed")


async def test_validation_failure_triggers_retry():
    """验证失败触发重试"""
    config = make_config(temperature_max_retries=3, backoff_max_retries=0)
    client = LLMClient(config)
    call_count = 0

    async def mock_chat(messages, temperature=0.1, max_tokens=None):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return (True, 'invalid json', '', (5, 10))
        return (True, '{"valid":true}', '', (5, 10))

    client.chat = mock_chat

    def validate(content):
        import json
        try:
            json.loads(content)
            return True, ''
        except:
            return False, f'JSON parse error: {content}'

    def build_retry(error, messages):
        return messages + [{"role": "assistant", "content": "retry"}]

    success, content, error, tokens = await client.chat_with_retry(
        [{"role": "user", "content": "hi"}],
        validate_response=validate,
        retry_messages_builder=build_retry,
    )
    assert success
    assert call_count == 3
    print("✅ test_validation_failure_triggers_retry passed")


async def test_attempts_counter():
    """total_attempts 随重试递增（供重试成本统计）"""
    config = make_config(temperature=0.7, temperature_step=0.1, temperature_max_retries=3, backoff_max_retries=0)
    client = LLMClient(config)
    call_temps = []

    async def mock_chat(messages, temperature=0.1, max_tokens=None):
        call_temps.append(temperature)
        if len(call_temps) < 3:
            return (False, '', 'API error', (0, 0))
        return (True, '{"result":"ok"}', '', (10, 20))

    client.chat = mock_chat
    success, content, error, tokens = await client.chat_with_retry([{"role": "user", "content": "hi"}])
    assert success
    assert client.get_stats()["total_attempts"] == 3
    print("✅ test_attempts_counter passed")


def pytest_approx(expected, rel=1e-6):
    """Simple approximation for floating point comparison"""
    class Approx:
        def __init__(self, expected, rel):
            self.expected = expected
            self.rel = rel
        def __eq__(self, other):
            return abs(other - self.expected) < self.rel
        def __repr__(self):
            return f"approx({self.expected})"
    return Approx(expected, rel)


async def main():
    await test_success_on_first_try()
    await test_temperature_annealing()
    await test_authentication_error_stops_immediately()
    await test_stop_requested()
    await test_validation_failure_triggers_retry()
    await test_attempts_counter()
    print("\n🎉 All LLM mock tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
