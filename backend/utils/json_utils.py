"""
JSON工具函数
提供安全的JSON提取和保存功能
（自旧项目 utils/json_utils.py 原样移植）
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def extract_json_from_text(text: str) -> Optional[str]:
    """
    从LLM响应中提取JSON字符串

    尝试多种策略：
    1. 直接解析（如果以{开头）
    2. 提取markdown代码块（```json ... ``` 或 ``` ... ```）
    3. 栈匹配找到正确的}闭合位置
    4. 正则匹配第一个{和最后一个}之间的内容

    Args:
        text: LLM响应文本

    Returns:
        提取的JSON字符串，失败返回None
    """
    text = text.strip()

    # 策略1: 直接解析
    if text.startswith('{'):
        return text

    # 策略2: 提取markdown代码块
    patterns = [
        r'```json\s*\n?(.*?)\n```',
        r'```\s*\n?(.*?)\n```'
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

    # 策略3: 找到第一个{位置
    start = text.find('{')
    if start == -1:
        return None

    # 策略4: 从start位置开始，用栈匹配找到正确的}闭合位置
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape_next:
            escape_next = False
            continue

        if ch == '\\':
            if in_string:
                escape_next = True
            continue

        if ch == '"' and not escape_next:
            in_string = not in_string
            continue

        if not in_string:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    # 策略5: 回退到原来的rfind方式（但不保证正确解析）
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    return None


def safe_parse_json(text: str) -> Optional[dict]:
    """
    安全地解析JSON字符串，支持多种容错策略（LLM输出不完美时的fallback链）

    内部委托 parse_json_robust，仅返回 dict 或 None（兼容既有调用方）。
    """
    data, _ = parse_json_robust(text)
    return data


def _replace_single_quoted_values(text: str) -> str:
    """
    将 JSON 中的单引号字符串值转换为双引号。
    例如: "key": 'value' → "key": "value"
    仅处理 : 后面的单引号值，不碰键名。
    """
    import re
    # 匹配 : 后面紧跟的单引号字符串值
    # 模式: : 空白 '内容' 后面跟 , 或 } 或换行
    return re.sub(
        r"""(:\s*)'([^']*?)'(\s*[,}\]])""",
        r'\1"\2"\3',
        text,
        flags=re.DOTALL
    )


_RE_KEY_MISSING_QUOTE = re.compile(r'([{,]\s*)([A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*)"\s*:')
_RE_VALUE_MISSING_QUOTE = re.compile(r'(:\s*)([^\s"{}[\],][^"{}[\]]*?)"(\s*[,}\]])')


def repair_missing_quotes(text: str) -> str:
    """定向修复 LLM 漏引号错误（模式在合法 JSON 中不可能出现，安全）：
    1) 键漏开引号: {id":2 -> {"id":2
    2) 值漏开引号: :"（内容）" -> :"（内容）"（值以全角字符/字母开头、只有闭引号）
    """
    fixed = _RE_KEY_MISSING_QUOTE.sub(lambda m: f'{m.group(1)}"{m.group(2)}":', text)
    fixed = _RE_VALUE_MISSING_QUOTE.sub(lambda m: f'{m.group(1)}"{m.group(2)}"{m.group(3)}', fixed)
    return fixed


def parse_json_robust(text: str):
    """
    安全解析 JSON 并返回诊断信息（LLM 输出容错链 + 漏引号定向修复）

    策略顺序说明（2026-08-07 优化）：
    漏引号定向修复（策略4）必须放在 json5/ast/json_repair 等 lenient 解析器之前。
    实测 json_repair 会把 `{"b":、伏笔线索"}` 静默"修复"成 `{"b":"伏笔线索"}`
    （丢全角字符）；lenient 解析器先跑会"错误成功"，定向修复永远轮不到。
    修复正则只在合法 JSON 不可能出现的模式上匹配，提前执行安全。

    Returns:
        (dict, None)                    — 直接解析成功
        (dict, "已自动修复JSON（漏引号）后解析成功") — 经漏引号修复成功
        (None, 诊断文本)                 — 全部失败，诊断含首次直接解析错误（含行号）
    """
    _log = logger
    if not text:
        return None, "空文本"

    first_error: Optional[str] = None
    text_len = len(text)

    # 策略1: 直接解析
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        first_error = str(e)

    # 策略2: 移除尾部内容（从最后一个完整的}之后）
    trimmed = text
    last_brace = text.rfind('}')
    if last_brace != -1:
        trimmed = text[:last_brace + 1]
        try:
            return json.loads(trimmed), None
        except json.JSONDecodeError:
            pass

    # 策略3: 移除尾部注释（// 风格）
    # B7 修复：在与切片相同的基准字符串上 search（防止行号偏移）
    base = trimmed
    comment_match = re.search(r'(}\s*)//.*$', base, re.MULTILINE | re.DOTALL)
    if comment_match:
        cleaned = base[:comment_match.start()].strip()
        try:
            return json.loads(cleaned), None
        except json.JSONDecodeError:
            pass

    # 策略4: 漏引号定向修复（最多两轮，每轮后重试标准解析与 json5）
    repaired = text
    for _ in range(2):
        next_repaired = repair_missing_quotes(repaired)
        if next_repaired == repaired:
            break
        repaired = next_repaired
        try:
            return json.loads(repaired), "已自动修复JSON（漏引号）后解析成功"
        except json.JSONDecodeError:
            pass
        try:
            import json5
            result = json5.loads(repaired)
            if isinstance(result, dict):
                return result, "已自动修复JSON（漏引号）后解析成功"
        except Exception:
            pass

    # 策略5: json5 — 处理单引号、尾逗号、注释、Python bool/None
    _log.info(f"JSON解析进入 json5 策略（文本长度={text_len}）")
    try:
        import json5
        result = json5.loads(text)
        if isinstance(result, dict):
            return result, None
    except ImportError:
        _log.debug("json5 未安装，跳过")
    except Exception as e:
        _log.debug(f"json5 解析失败: {e}")

    # 策略6: ast.literal_eval — 处理单引号字符串（Python dict 格式）
    _log.info(f"JSON解析进入 ast.literal_eval 策略（文本长度={text_len}）")
    try:
        import ast
        result = ast.literal_eval(text)
        if isinstance(result, dict):
            return result, None
    except Exception as e:
        _log.info(f"ast.literal_eval 解析失败: {e}")

    # 策略7: json_repair
    _log.info(f"JSON解析进入 json_repair 策略（文本长度={text_len}）")
    try:
        import json_repair
        result = json_repair.loads(text)
        if isinstance(result, dict):
            return result, None
    except ImportError:
        _log.debug("json_repair 未安装，跳过")
    except Exception as e:
        _log.debug(f"json_repair 解析失败: {e}")

    # 策略8: 单引号→双引号替换（保守：只替换键值对中的单引号值）
    try:
        fixed = _replace_single_quoted_values(text)
        if fixed != text:
            return json.loads(fixed), None
    except json.JSONDecodeError:
        pass

    return None, first_error or "JSON解析失败（未知原因）"


def safe_save_json(data: Any, file_path: Path, ensure_ascii: bool = False) -> bool:
    """
    原子性保存JSON文件（先写临时文件再替换）

    Args:
        data: 要保存的数据
        file_path: 目标文件路径
        ensure_ascii: 是否确保ASCII编码

    Returns:
        是否成功
    """
    try:
        temp_path = file_path.with_suffix('.tmp')

        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=ensure_ascii, indent=2, default=str)

        # 原子替换
        temp_path.replace(file_path)
        return True

    except Exception as e:
        logger.error(f"保存JSON失败: {e}")
        # 清理临时文件
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        return False


def safe_load_json(file_path: Path, default: Any = None) -> Any:
    """
    安全地加载JSON文件

    Args:
        file_path: 文件路径
        default: 失败时的默认值

    Returns:
        解析后的数据，失败返回default
    """
    if not file_path.exists():
        return default

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载JSON失败: {e}")
        return default
