"""P1 修复验证（2026-08-24）：APIError.body 为非 dict truthy 值时
审核嗅探不得崩溃（原实现 body.get("code") AttributeError 穿透重试链）。"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.core.llm_client import moderation_hit_from_exception


def test_string_body_does_not_crash():
    e = SimpleNamespace(code=None, body="Bad Gateway HTML", message="Bad Gateway HTML")
    assert moderation_hit_from_exception(e) is False


def test_list_body_does_not_crash():
    e = SimpleNamespace(code=None, body=[{"detail": "err"}], message="err")
    assert moderation_hit_from_exception(e) is False


def test_dict_body_error_message_detected():
    e = SimpleNamespace(code=None, body={"error": {"message": "content filtered by policy"}})
    assert moderation_hit_from_exception(e) is True


def test_dict_body_code_detected():
    e = SimpleNamespace(code=None, body={"code": "content_filter"})
    assert moderation_hit_from_exception(e) is True


def test_none_body_ok():
    e = SimpleNamespace(code=None, body=None, message="timeout")
    assert moderation_hit_from_exception(e) is False
