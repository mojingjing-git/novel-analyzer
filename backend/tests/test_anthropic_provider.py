"""
测试 Anthropic 格式支持：provider 检测 + 载荷构造 + 响应解析（mock）
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config.settings import APIConfig
from backend.core.llm_client import LLMClient, detect_provider


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
        provider="auto",
    )
    defaults.update(kwargs)
    return APIConfig(**defaults)


def test_detect_provider():
    """auto 检测：URL/Key/模型名特征 + 显式覆盖"""
    # URL 含 anthropic
    assert detect_provider(make_config(base_url="https://api.anthropic.com")) == "anthropic"
    # sk-ant- 前缀
    assert detect_provider(make_config(api_key="sk-ant-abc123")) == "anthropic"
    # claude- 模型名
    assert detect_provider(make_config(model="claude-sonnet-4-5")) == "anthropic"
    # 显式覆盖
    assert detect_provider(make_config(provider="anthropic")) == "anthropic"
    assert detect_provider(make_config(provider="openai", api_key="sk-ant-abc")) == "openai"
    # 其余默认 openai
    assert detect_provider(make_config()) == "openai"
    print("✅ test_detect_provider passed")


def test_anthropic_payload_building():
    """system 提取到顶层参数、同角色消息合并、thinking 透传"""
    client = LLMClient(make_config(
        provider="anthropic",
        model="claude-3-5-sonnet",
        thinking_mode={"thinking": {"type": "enabled", "budget_tokens": 1024}},
    ))
    payload = client._build_anthropic_payload(
        [
            {"role": "system", "content": "你是分析器"},
            {"role": "user", "content": "问题1"},
            {"role": "user", "content": "问题2"},
            {"role": "assistant", "content": "回答"},
            {"role": "user", "content": "问题3"},
        ],
        temperature=0.5,
        max_tokens=800,
    )
    assert payload["model"] == "claude-3-5-sonnet"
    assert payload["max_tokens"] == 800
    assert payload["temperature"] == 0.5
    assert payload["system"] == "你是分析器"
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    # 相邻同角色合并
    assert payload["messages"] == [
        {"role": "user", "content": "问题1\n\n问题2"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": "问题3"},
    ]
    print("✅ test_anthropic_payload_building passed")


def test_anthropic_response_parsing():
    """text block 拼接、thinking block 丢弃、usage 映射"""
    client = LLMClient(make_config(provider="anthropic"))
    resp = MagicMock()
    t1 = MagicMock()
    t1.type = "text"
    t1.text = '{"core_events": []}'
    t2 = MagicMock()
    t2.type = "thinking"
    t2.thinking = "思考过程"
    t3 = MagicMock()
    t3.type = "text"
    t3.text = " 补充"
    resp.content = [t1, t2, t3]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 50
    resp.usage.cache_read_input_tokens = 30
    content, pt, ct, cached = client._parse_anthropic_response(resp)
    assert content == '{"core_events": []} 补充'
    assert (pt, ct, cached) == (100, 50, 30)
    print("✅ test_anthropic_response_parsing passed")


async def test_anthropic_chat_success():
    """anthropic 分支：messages.create 被调用且返回解析后的内容"""
    client = LLMClient(make_config(provider="anthropic", model="claude-sonnet-4-5"))
    fake_anthropic = MagicMock()
    resp = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = '{"ok": true}'
    resp.content = [block]
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 20
    resp.usage.cache_read_input_tokens = 0
    fake_anthropic.messages.create = AsyncMock(return_value=resp)
    client.anthropic = fake_anthropic

    success, content, error, tokens = await client.chat(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        temperature=0.3,
    )
    assert success
    assert content == '{"ok": true}'
    assert tokens == (10, 20)
    # 断言 create 收到的载荷
    call_kwargs = fake_anthropic.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-5"
    assert call_kwargs["system"] == "sys"
    assert call_kwargs["messages"] == [{"role": "user", "content": "hi"}]
    print("✅ test_anthropic_chat_success passed")


async def test_anthropic_empty_content():
    """anthropic 空内容（无 text block）→ 失败"""
    client = LLMClient(make_config(provider="anthropic"))
    fake_anthropic = MagicMock()
    resp = MagicMock()
    resp.content = []
    resp.usage.input_tokens = 5
    resp.usage.output_tokens = 0
    resp.usage.cache_read_input_tokens = 0
    fake_anthropic.messages.create = AsyncMock(return_value=resp)
    client.anthropic = fake_anthropic
    success, _c, error, _t = await client.chat([{"role": "user", "content": "hi"}])
    assert not success
    assert "空内容" in error
    print("✅ test_anthropic_empty_content passed")


async def test_openai_path_unchanged():
    """provider=openai 时仍走 OpenAI 客户端"""
    client = LLMClient(make_config())
    assert client.client is not None
    assert client.anthropic is None
    assert client.provider == "openai"
    fake_openai = MagicMock()
    resp = MagicMock()
    msg = MagicMock()
    msg.content = '{"ok": true}'
    resp.choices = [MagicMock(message=msg)]
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 20
    resp.usage.total_tokens = 30
    resp.usage.prompt_tokens_details = None
    fake_openai.chat.completions.create = AsyncMock(return_value=resp)
    client.client = fake_openai
    success, content, error, tokens = await client.chat([{"role": "user", "content": "hi"}])
    assert success
    assert content == '{"ok": true}'
    assert tokens == (10, 20)
    print("✅ test_openai_path_unchanged passed")


async def run_all():
    test_detect_provider()
    test_anthropic_payload_building()
    test_anthropic_response_parsing()
    await test_anthropic_chat_success()
    await test_anthropic_empty_content()
    await test_openai_path_unchanged()


if __name__ == "__main__":
    asyncio.run(run_all())

async def test_probe_thinking_finds_working_param():
    """探测：找到有效的禁用思考参数"""
    from backend.core.llm_client import LLMClient
    fake_client = MagicMock()

    def make_resp(content_len, reasoning_len):
        resp = MagicMock()
        msg = MagicMock()
        msg.content = "x" * content_len
        msg.reasoning_content = "r" * reasoning_len
        resp.choices = [MagicMock(message=msg)]
        return resp

    # 基线(思考) → thinking:disabled(有效) → reasoning_effort(无效) → enable_thinking(无效)
    side_effects = [
        make_resp(3, 200),   # 基线：有思考
        make_resp(3, 0),     # thinking disabled：content 有、reasoning 空 → 有效
        make_resp(3, 150),   # reasoning_effort：思考还在 → 无效
        make_resp(3, 180),   # enable_thinking：思考还在 → 无效
    ]
    fake_client.chat.completions.create = AsyncMock(side_effect=side_effects)

    with patch("backend.core.llm_client.AsyncOpenAI", return_value=fake_client):
        result = await LLMClient.probe_thinking_params("https://api.test.com", "k", "m", "auto")

    assert result["default_thinks"] is True
    assert result["best"]["thinking_mode"] == {"thinking": {"type": "disabled"}}
    worked = [r for r in result["results"] if r["worked"]]
    assert len(worked) == 1
    print("✅ test_probe_thinking_finds_working_param passed")


async def test_probe_thinking_none_works():
    """探测：无参数有效 → best=None + note"""
    from backend.core.llm_client import LLMClient
    fake_client = MagicMock()

    def make_resp(reasoning_len):
        resp = MagicMock()
        msg = MagicMock()
        msg.content = "x" * 3
        msg.reasoning_content = "r" * reasoning_len
        resp.choices = [MagicMock(message=msg)]
        return resp

    fake_client.chat.completions.create = AsyncMock(side_effect=[
        make_resp(200), make_resp(180), make_resp(190), make_resp(170),
    ])
    with patch("backend.core.llm_client.AsyncOpenAI", return_value=fake_client):
        result = await LLMClient.probe_thinking_params("https://api.test.com", "k", "m", "auto")

    assert result["best"] is None
    assert "未探测到" in result["note"]
    print("✅ test_probe_thinking_none_works passed")


async def test_probe_thinking_model_no_think():
    """探测：模型默认不思考 → 无需禁用"""
    from backend.core.llm_client import LLMClient
    fake_client = MagicMock()
    resp = MagicMock()
    msg = MagicMock()
    msg.content = "x" * 5
    msg.reasoning_content = ""
    resp.choices = [MagicMock(message=msg)]
    fake_client.chat.completions.create = AsyncMock(return_value=resp)

    with patch("backend.core.llm_client.AsyncOpenAI", return_value=fake_client):
        result = await LLMClient.probe_thinking_params("https://api.test.com", "k", "m", "auto")

    assert result["default_thinks"] is False
    assert result["best"] is None
    assert "无需禁用" in result["note"]
    print("✅ test_probe_thinking_model_no_think passed")


async def test_probe_thinking_anthropic_skip():
    """anthropic 协议直接返回官方参数，不发请求"""
    from backend.core.llm_client import LLMClient
    with patch("backend.core.llm_client.AsyncOpenAI") as fake_cls:
        result = await LLMClient.probe_thinking_params("https://api.anthropic.com", "k", "claude-x", "auto")
        fake_cls.assert_not_called()
    assert result["best"]["thinking_mode"] == {"thinking": {"type": "disabled"}}
    print("✅ test_probe_thinking_anthropic_skip passed")

def test_anthropic_payload_whitelist():
    """#4: thinking_mode 只放行 thinking 键，enable_thinking 残留不得透传"""
    client = LLMClient(make_config(
        provider="anthropic",
        thinking_mode={"enable_thinking": False, "thinking": {"type": "disabled"}},
    ))
    payload = client._build_anthropic_payload(
        [{"role": "user", "content": "hi"}], temperature=0.5, max_tokens=100)
    assert payload["thinking"] == {"type": "disabled"}
    assert "enable_thinking" not in payload
    # 只有 enable_thinking 时：不注入任何额外字段
    client2 = LLMClient(make_config(provider="anthropic", thinking_mode={"enable_thinking": False}))
    payload2 = client2._build_anthropic_payload(
        [{"role": "user", "content": "hi"}], temperature=0.5, max_tokens=100)
    assert "thinking" not in payload2
    assert "enable_thinking" not in payload2
    print("✅ test_anthropic_payload_whitelist passed")


def test_anthropic_total_tokens_include_cache():
    """#3: anthropic total = input + output + cache（对齐 openai total_tokens 口径）"""
    client = LLMClient(make_config(provider="anthropic"))
    resp = MagicMock()
    resp.content = []
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 50
    resp.usage.cache_read_input_tokens = 30
    _c, pt, ct, cached = client._parse_anthropic_response(resp)
    assert (pt, ct, cached) == (100, 50, 30)
    print("✅ test_anthropic_total_tokens_include_cache passed")
