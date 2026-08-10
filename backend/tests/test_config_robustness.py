"""
测试 ConfigManager BOM 容错（#1）与 anthropic list_models 空 base_url 守卫（#5）
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config.settings import ConfigManager
from backend.core.llm_client import LLMClient


def test_config_load_with_bom(tmp_path):
    """#1: 带 UTF-8 BOM 的 config.json 应正常加载（utf-8-sig）"""
    cfg_path = tmp_path / "config.json"
    data = {"api": {"model": "test-model", "base_url": "https://api.test.com",
                    "api_key": "k", "provider": "auto"}}
    with open(cfg_path, "wb") as f:
        f.write(b"\xef\xbb\xbf" + json.dumps(data, ensure_ascii=False).encode("utf-8"))
    mgr = ConfigManager(cfg_path)
    cfg = mgr.load()
    assert cfg.api.model == "test-model"
    assert cfg.api.base_url == "https://api.test.com"
    print("✅ test_config_load_with_bom passed")


def test_config_load_corrupt_logs_error(tmp_path, caplog):
    """#1: 损坏的 config.json 必须记 ERROR 日志而非静默 print"""
    import logging
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"api": {"model": "x",', encoding="utf-8")  # 截断的 JSON
    mgr = ConfigManager(cfg_path)
    with caplog.at_level(logging.ERROR, logger="backend.config.settings"):
        cfg = mgr.load()
    assert cfg.api.model == ""  # 回退默认
    assert any("配置加载失败" in r.message for r in caplog.records)
    print("✅ test_config_load_corrupt_logs_error passed")


async def test_list_models_anthropic_empty_base_url():
    """#5: anthropic 格式且 base_url 为空 → 直接返回空列表，不发 GET /models"""
    from backend.core.llm_client import LLMClient
    with patch("backend.core.llm_client.AsyncOpenAI") as fake_cls:
        result = await LLMClient.list_models("", "k", "anthropic")
    assert result == []
    fake_cls.assert_not_called()
    print("✅ test_list_models_anthropic_empty_base_url passed")
