"""P2：网关以 OpenAI 协议暴露 claude-* 时，预检应探测出正确协议并就地纠正"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config.settings import AppConfig
from backend.services.queue_service import QueueManager


def _cfg(provider="auto"):
    c = AppConfig()
    c.api.model = "claude-3-5-sonnet"
    c.api.base_url = "https://gw.example.com/v1"
    c.api.provider = provider
    return c


def _svc():
    # check_api 定义在 QueueManager 上（AnalysisService 经 self.queue 调用），
    # 且不依赖实例状态——用 __new__ 绕过构造器
    return QueueManager.__new__(QueueManager)


def test_cross_protocol_probe_heals_and_returns_true(monkeypatch):
    calls = []

    async def fake_list_models(base_url, api_key, provider):
        calls.append(provider)
        if provider == "openai":
            return ["claude-3-5-sonnet"]
        raise RuntimeError("404 not found")

    monkeypatch.setattr(
        "backend.services.queue_service.LLMClient.list_models", fake_list_models)
    # 原生 chat 探针封掉（避免真实网络调用）：无论何种 provider 都失败
    async def fake_chat_fail(self, messages, *a, **k):
        return False, "", "down", (0, 0)
    monkeypatch.setattr(
        "backend.services.queue_service.LLMClient.chat", fake_chat_fail)

    cfg = _cfg()
    ok = asyncio.run(_svc().check_api(cfg))
    assert ok is True
    assert cfg.api.provider == "openai", "预检成功后应就地纠正 provider"


def test_both_protocols_fail_returns_false(monkeypatch):
    async def always_fail(base_url, api_key, provider):
        raise RuntimeError("down")

    monkeypatch.setattr(
        "backend.services.queue_service.LLMClient.list_models", always_fail)
    # 同时封掉 chat 探针与其翻转复试
    class FakeClient:
        def __init__(self, config): pass
        async def chat(self, messages):
            return False, "", "down", (0, 0)
    monkeypatch.setattr(
        "backend.services.queue_service.LLMClient", FakeClient)

    cfg = _cfg()
    ok = asyncio.run(_svc().check_api(cfg))
    assert ok is False
    assert cfg.api.provider in ("auto", "anthropic"), "全败时不得盲目改写 provider"
