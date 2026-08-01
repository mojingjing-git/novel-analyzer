"""测试漏引号修复与 parse_json_robust 诊断信息"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.utils.json_utils import repair_missing_quotes, parse_json_robust


def test_key_missing_opening_quote():
    """键漏开引号：{id":2 -> {"id":2（今天日志里的真实样本形态）"""
    broken = '{"core_events":[{"id":1,"event":"a"},{id":2,"event":"b"}],"ok":true}'
    data, detail = parse_json_robust(broken)
    assert data is not None
    assert data["core_events"][1]["id"] == 2
    assert data["core_events"][1]["event"] == "b"
    assert detail == "已自动修复JSON（漏引号）后解析成功"


def test_value_missing_opening_quote_ideographic_comma():
    """值漏开引号且以、开头：ast 曾报 invalid character '、'(U+3001)"""
    broken = '{"a":1,"b":、伏笔线索"}'
    data, detail = parse_json_robust(broken)
    assert data is not None
    assert data["b"] == "、伏笔线索"
    assert detail == "已自动修复JSON（漏引号）后解析成功"


def test_value_missing_opening_quote_fullwidth_paren():
    """值漏开引号且以（开头：ast 曾报 invalid character '（'(U+FF08)"""
    broken = '{"a":1,"b":（重要内容）"}'
    data, detail = parse_json_robust(broken)
    assert data is not None
    assert data["b"] == "（重要内容）"
    assert detail == "已自动修复JSON（漏引号）后解析成功"


def test_valid_json_not_touched():
    """合法 JSON 不被误伤（含嵌套、数组、Unicode、转义引号）"""
    text = '{"a":{"b":[1,2],"c":"x,y"},"d":"\\"esc\\"","e":"中文"}'
    assert repair_missing_quotes(text) == text
    data, detail = parse_json_robust(text)
    assert data is not None
    assert detail is None


def test_boolean_and_number_values_not_touched():
    """布尔/数字值不受值修复正则影响"""
    text = '{"a":true,"b":123,"c":[true,false],"d":null}'
    assert repair_missing_quotes(text) == text


def test_empty_text_detail():
    """空文本返回明确诊断"""
    data, detail = parse_json_robust("")
    assert data is None
    assert detail == "空文本"


def test_python_dict_style_still_works():
    """原有 json5/ast 兼容能力不回退：单引号 Python 字典格式"""
    data, detail = parse_json_robust("{'a': 'x', 'b': 1}")
    assert data is not None
    assert data["a"] == "x"
