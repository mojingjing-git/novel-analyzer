"""
内容审核拦截识别（provider 无关）

三层检测：
1. 结构化错误码（各 SDK 异常上的 error.code / 状态码语义）
2. 自由文本关键词（智谱/小米等返回自然语言，中英文都覆盖）
3. （在 llm_client 层处理）隐式信号：content_filter / 空内容

标记以 [MODERATION] 前缀注入错误字符串，对调用方零侵入传递。
"""

import re

MODERATION_MARKER = "[MODERATION]"

# 1) 结构化错误码（各家实测/文档样例）
MODERATION_CODES = {
    # MiniMax：1026=输入内容涉敏，1027=输出内容涉敏
    "1026", "1027",
    # OpenAI 兼容系 error.code
    "content_policy_violation", "content_filter", "safety",
    "moderation", "policy_violation", "unsupported_content",
    "inappropriate_content", "harmful_content",
}

# 2) 自由文本关键词（大小写不敏感，正则片段）
MODERATION_TEXT_PATTERNS = [
    # 中文
    r"安全规范", r"内容审核", r"内容安全", r"敏感", r"违规", r"不合规",
    r"不符合.{0,6}规范", r"被过滤", r"拒绝回答", r"涉敏", r"高风险",
    r"风险内容", r"无法生成.{0,8}内容", r"涉及.{0,6}(色情|暴力|政治)",
    # 英文
    r"high risk", r"safety policy", r"moderation", r"sensitive",
    r"content filter", r"filtered", r"prohibited", r"blocked",
    r"violat", r"inappropriat", r"harmful", r"unsafe", r"unacceptable",
]
_MOD_TEXT_RE = re.compile("|".join(MODERATION_TEXT_PATTERNS), re.IGNORECASE)


def is_moderation_code(code) -> bool:
    """结构化错误码判定（code 可为 str/int/None）"""
    if code is None:
        return False
    return str(code).strip() in MODERATION_CODES


def is_moderation_message(message) -> bool:
    """自由文本关键词判定"""
    if not message:
        return False
    return bool(_MOD_TEXT_RE.search(str(message)))


def mark_moderation(error: str) -> str:
    """给错误字符串加 [MODERATION] 标记（幂等）"""
    error = error or ""
    if is_moderation_error(error):
        return error
    return f"{MODERATION_MARKER} {error}"


def is_moderation_error(error: str) -> bool:
    """检查错误字符串是否带审核标记"""
    return bool(error) and error.startswith(MODERATION_MARKER)
