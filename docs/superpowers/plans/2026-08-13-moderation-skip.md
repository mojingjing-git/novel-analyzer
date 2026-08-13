# 内容审核拦截自动识别与跳过 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 分析阶段自动识别各家 LLM 的内容审核拦截（显式错误码/自由文本/隐式空内容），对拦截章节重试 1 次后跳过并标记，不再白烧重试成本。

**Architecture:** 新增 provider 无关的审核分类器模块（三层检测：结构化错误码 → 中英文关键词 → 隐式信号），在 llm_client 各错误分支注入 `[MODERATION]` 标记；chat_with_retry 见标记重试 1 次后短路；pipeline 将带标记的失败块标记为 skipped（不进失败集、不补跑），完成统计输出 skipped 列表。

**Tech Stack:** Python 3.13 asyncio / FastAPI / Vue3（仅 DTO+设置项）/ pytest

## Global Constraints

- 不改 `chat()` 的返回签名 `(bool, str, str, Tuple[int,int])`——通过错误字符串内嵌 `[MODERATION]` 标记传递，避免波及 analyzer/pipeline/final_summary 全部调用点
- 错误分类器集中在新建 `backend/core/moderation.py`，关键词表注释标注各家实测样例（MiniMax 1026/1027、智谱中文安全规范、小米 high risk、OpenAI content_filter）
- 新配置字段 `analysis.skip_moderation_blocked: bool = True`，前端 AppConfigDto 必须同步（PUT 全量替换会丢未声明字段）
- 断点续跑不持久化 skipped 状态（重跑会重新判定，天然自愈）；skipped 不写入 `chapter_*_failed.json`
- 新增失败日志分类 `ModerationBlocked`（api_failures.log 可统计）
- 全量回归基线：`python -m pytest`（当前 79 passed）；前端 `npm run build`

---

### Task 1: 审核分类器模块

**Files:**
- Create: `backend/core/moderation.py`
- Test: `backend/tests/test_moderation.py`

**Interfaces:**
- Produces:
  - `MODERATION_MARKER = "[MODERATION]"`
  - `def is_moderation_code(code) -> bool` — 结构化错误码判定
  - `def is_moderation_message(message) -> bool` — 中英文关键词判定
  - `def mark_moderation(error: str) -> str` — 给错误串加标记（幂等）
  - `def is_moderation_error(error: str) -> bool` — 检查标记

- [ ] **Step 1: 写失败测试**

`backend/tests/test_moderation.py`:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.core.moderation import (
    is_moderation_code, is_moderation_message,
    mark_moderation, is_moderation_error,
)


def test_structured_codes():
    # MiniMax 1026/1027（输入/输出涉敏）
    assert is_moderation_code("1026")
    assert is_moderation_code("1027")
    # OpenAI 系错误码
    assert is_moderation_code("content_policy_violation")
    assert is_moderation_code("content_filter")
    assert is_moderation_code("safety")
    # 非审核错误码
    assert not is_moderation_code("1008")       # 余额不足
    assert not is_moderation_code("rate_limit_exceeded")


def test_chinese_keywords():
    # 智谱风格中文消息
    assert is_moderation_message("内容不符合安全规范")
    assert is_moderation_message("请求内容包含敏感信息，已拒绝生成")
    assert is_moderation_message("高风险内容，无法生成")
    assert is_moderation_message("当前内容涉及违规")
    assert not is_moderation_message("服务器繁忙，请稍后再试")


def test_english_keywords():
    # 小米/OpenAI 风格英文消息
    assert is_moderation_message("high risk content detected")
    assert is_moderation_message("This request violates our safety policy")
    assert is_moderation_message("content filtered by moderation")
    assert not is_moderation_message("The model is overloaded, retry later")


def test_marker_roundtrip():
    err = "API错误 (HTTP 400): bad"
    marked = mark_moderation(err)
    assert marked.startswith("[MODERATION]")
    assert is_moderation_error(marked)
    assert not is_moderation_error(err)
    # 幂等
    assert mark_moderation(marked) == marked
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest backend/tests/test_moderation.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'backend.core.moderation'`）

- [ ] **Step 3: 实现分类器**

`backend/core/moderation.py`:
```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest backend/tests/test_moderation.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/core/moderation.py backend/tests/test_moderation.py
git commit -m "feat: 内容审核分类器（错误码+中英文关键词）"
```

---

### Task 2: chat() 错误分支注入标记

**Files:**
- Modify: `backend/core/llm_client.py`（import、OpenAI APIError 分支、anthropic APIStatusError 分支、空 choices 分支、空内容分支）
- Test: `backend/tests/test_moderation.py`（追加）

**Interfaces:**
- Consumes: `mark_moderation` / `is_moderation_code` / `is_moderation_message`（Task 1）
- Produces: 失败错误串含 `[MODERATION]` 前缀的 chat() 返回；`_is_429` 等既有方法不变

- [ ] **Step 1: 写失败测试（追加到 test_moderation.py）**

```python
from unittest.mock import MagicMock, patch
import pytest


def _api_error(code=None, status=400, msg="bad"):
    e = MagicMock()
    e.status_code = status
    e.code = code
    e.body = {"error": {"code": code, "message": msg}} if code else {"error": {"message": msg}}
    e.response = MagicMock()
    e.response.headers = {}
    e.__str__ = lambda self: msg
    return e


async def test_chat_api_error_moderation_marked():
    """OpenAI APIError 带 content_policy_violation → 错误串含 [MODERATION]"""
    from backend.core.llm_client import LLMClient, APIError
    from backend.config.settings import APIConfig
    cfg = APIConfig(base_url="https://api.test.com", api_key="k", model="m",
                    max_tokens=100, timeout=5, temperature_max_retries=1, backoff_max_retries=0)
    client = LLMClient(cfg)
    fake = MagicMock()
    fake.chat.completions.create = AsyncMock(side_effect=_api_error(code="content_policy_violation"))
    client.client = fake
    ok, content, error, tokens = await client.chat([{"role": "user", "content": "hi"}])
    assert not ok
    assert "[MODERATION]" in error


async def test_chat_error_text_moderation_marked():
    """通用异常消息含中文安全词 → 标记"""
    from backend.core.llm_client import LLMClient
    from backend.config.settings import APIConfig
    cfg = APIConfig(base_url="https://api.test.com", api_key="k", model="m",
                    max_tokens=100, timeout=5, temperature_max_retries=1, backoff_max_retries=0)
    client = LLMClient(cfg)
    fake = MagicMock()
    fake.chat.completions.create = AsyncMock(side_effect=RuntimeError("内容不符合安全规范"))
    client.client = fake
    ok, content, error, tokens = await client.chat([{"role": "user", "content": "hi"}])
    assert not ok
    assert "[MODERATION]" in error
```

（`AsyncMock` 需 `from unittest.mock import AsyncMock` 追加到文件顶部；`import pytest` 用于 asyncio 测试需确认项目 asyncio_mode——若 pytest.ini 未开 asyncio 自动模式，改用 `asyncio.run()` 包一层，参考 test_anthropic_provider.py 的既有写法。）

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest backend/tests/test_moderation.py -q`
Expected: 新增 2 个用例 FAIL（错误串无标记）

- [ ] **Step 3: 实现接线**

`backend/core/llm_client.py`：
```python
# 顶部 import 追加
from ..core.moderation import (
    mark_moderation, is_moderation_code, is_moderation_message,
)
```

OpenAI `except APIError as e:` 分支（status_code 提取后）：
```python
        except APIError as e:
            status_code = getattr(e, 'status_code', None)
            error_msg = f"API错误 (HTTP {status_code}): {str(e)}"

            # 内容审核拦截识别：结构化错误码 / 响应体 message / 异常文本
            e_code = getattr(e, 'code', None)
            body = getattr(e, 'body', None) or {}
            body_msg = ""
            if isinstance(body, dict):
                err_inner = body.get("error") or {}
                body_msg = str(err_inner.get("message", "")) if isinstance(err_inner, dict) else str(body)
            if (is_moderation_code(e_code) or is_moderation_code(body.get("code"))
                    or is_moderation_message(body_msg) or is_moderation_message(str(e))):
                error_msg = mark_moderation(error_msg)
                logger.warning("检测到内容审核拦截，将重试1次后跳过该章节")

            if status_code == 429:
                ...（既有 Retry-After 逻辑不变）...
```

anthropic `except anthropic.APIStatusError as e:` 分支（status_code 提取后）：
```python
            # 内容审核拦截识别（Anthropic usage policy 类）
            body = getattr(e, 'response', None)
            body_text = ""
            if body is not None and hasattr(body, 'text'):
                body_text = body.text
            if is_moderation_message(body_text) or is_moderation_message(str(e)):
                error_msg = mark_moderation(error_msg)
                logger.warning("检测到内容审核拦截，将重试1次后跳过该章节")
```

通用 `except Exception as e:` 分支（error_msg 构造后）：
```python
            if is_moderation_message(error_msg):
                error_msg = mark_moderation(error_msg)
                logger.warning("检测到内容审核拦截，将重试1次后跳过该章节")
```

OpenAI 空 choices 分支（`if not response.choices:`）：
```python
            if not response.choices:
                error_msg = "API返回空choices列表（可能触发内容过滤）"
                logger.warning(error_msg)
                # 空 choices 通常是审核过滤（content_filter），按拦截处理
                error_msg = mark_moderation(error_msg)
                return False, "", error_msg, (0, 0)
```

Anthropic 空内容分支（`if not content:`，在 `_parse_anthropic_response` 后）：
```python
                if not content:
                    # stop_reason=refusal 或空内容：视为审核拦截（重试1次后跳过）
                    stop_reason = getattr(response, "stop_reason", None) or ""
                    error_msg = "API返回空内容（可能触发内容过滤）"
                    if stop_reason == "refusal" or is_moderation_message(str(stop_reason)):
                        error_msg = mark_moderation(error_msg)
                        logger.warning("检测到内容审核拦截（空内容/refusal），将重试1次后跳过该章节")
                    logger.warning(error_msg)
                    return False, "", error_msg, (0, 0)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest backend/tests/test_moderation.py -q`
Expected: PASS（6 passed）；再跑 `python -m pytest backend/tests/test_anthropic_provider.py backend/tests/test_llm_mock.py -q` 确认既有 mock 不回归

- [ ] **Step 5: 提交**

```bash
git add backend/core/llm_client.py backend/tests/test_moderation.py
git commit -m "feat: chat() 错误分支注入内容审核标记"
```

---

### Task 3: chat_with_retry 重试1次后短路

**Files:**
- Modify: `backend/core/llm_client.py`（chat_with_retry）
- Test: `backend/tests/test_moderation.py`（追加）

**Interfaces:**
- Consumes: `is_moderation_error`（Task 1）
- Produces: 连续 2 次 moderation → 立即返回（总尝试数=2，不走退避）；失败日志 error_type=`ModerationBlocked`

- [ ] **Step 1: 写失败测试（追加）**

```python
async def test_retry_once_then_shortcircuit():
    """审核拦截：第1次失败→重试1次→仍拦截→短路返回，不进入退避链"""
    from backend.core.llm_client import LLMClient
    from backend.config.settings import APIConfig
    from backend.core.moderation import mark_moderation
    cfg = APIConfig(base_url="https://api.test.com", api_key="k", model="m",
                    max_tokens=100, timeout=5,
                    temperature_max_retries=2, backoff_max_retries=1)
    client = LLMClient(cfg)
    err = mark_moderation("API错误 (HTTP 400): content policy")
    client.chat = AsyncMock(side_effect=[
        (False, "", err, (0, 0)),
        (False, "", err, (0, 0)),
    ])
    with patch("asyncio.sleep", new=AsyncMock()):
        ok, content, error, tokens, stats = await client.chat_with_retry(
            [{"role": "user", "content": "hi"}])
    assert not ok
    assert "[MODERATION]" in error
    assert stats["attempts"] == 2          # 只重试1次，不进退避（否则会是 3+）
    assert client.chat.call_count == 2


async def test_retry_once_success():
    """审核拦截后第 2 次成功 → 正常返回（偶发误判可自愈）"""
    from backend.core.llm_client import LLMClient
    from backend.config.settings import APIConfig
    from backend.core.moderation import mark_moderation
    cfg = APIConfig(base_url="https://api.test.com", api_key="k", model="m",
                    max_tokens=100, timeout=5,
                    temperature_max_retries=2, backoff_max_retries=1)
    client = LLMClient(cfg)
    err = mark_moderation("API错误 (HTTP 400): content policy")
    client.chat = AsyncMock(side_effect=[
        (False, "", err, (0, 0)),
        (True, '{"ok":1}', "", (1, 1)),
    ])
    with patch("asyncio.sleep", new=AsyncMock()):
        ok, content, error, tokens, stats = await client.chat_with_retry(
            [{"role": "user", "content": "hi"}])
    assert ok
    assert stats["attempts"] == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest backend/tests/test_moderation.py -q`
Expected: 新增 2 用例 FAIL（attempts 为 3+/行为不符）

- [ ] **Step 3: 实现短路**

`chat_with_retry` 温度退火循环内、`last_error = error` 之后（认证检查之前）：
```python
            last_error = error
            logger.warning(f"❌ 尝试失败: {error}")

            # 内容审核拦截：重试1次后短路（第1次失败→下一轮尝试；第2次仍拦截→立即放弃，
            # 不进入指数退避，避免对"永久拒绝"白等）
            if is_moderation_error(error):
                if moderation_hits >= 1:
                    _get_failure_logger().record_failure(
                        attempt_num=total_attempts,
                        max_retries=self.config.temperature_max_retries + self.config.backoff_max_retries,
                        temperature=temp,
                        error_type="ModerationBlocked",
                        error_message=error,
                        messages_length=messages_length,
                    )
                    logger.warning("⛔ 内容审核拦截（连续2次），跳过该章节，不再重试")
                    return False, "", error, last_consumed_tokens, {"attempts": total_attempts, "failed_tokens": call_failed_tokens}
                moderation_hits += 1
```

并在函数开头初始化：`moderation_hits = 0`（与 `last_error = ""` 并列）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest backend/tests/test_moderation.py -q`
Expected: PASS（8 passed）；全量 `python -m pytest -q` 确认无回归

- [ ] **Step 5: 提交**

```bash
git add backend/core/llm_client.py backend/tests/test_moderation.py
git commit -m "feat: 内容审核拦截重试1次后短路（ModerationBlocked 统计）"
```

---

### Task 4: analyzer 错误传播到 pipeline

**Files:**
- Modify: `backend/core/analyzer.py`（analyze_chapter 失败路径）
- Test: `backend/tests/test_moderation.py`（追加）

**Interfaces:**
- Produces: `retry_info["error"]` 字段（失败时携带原始错误串，含 `[MODERATION]` 标记）

- [ ] **Step 1: 写失败测试（追加）**

```python
async def test_analyzer_propagates_moderation_error():
    """analyzer 失败时 retry_info.error 携带标记（pipeline 据此判 skipped）"""
    from backend.core.analyzer import NovelAnalyzer
    from backend.config.settings import APIConfig
    from backend.core.moderation import mark_moderation
    cfg = APIConfig(base_url="https://api.test.com", api_key="k", model="m",
                    max_tokens=100, timeout=5, temperature_max_retries=1, backoff_max_retries=0)
    analyzer = NovelAnalyzer(cfg)
    analyzer.llm_client = MagicMock()
    analyzer.llm_client.chat_with_retry = AsyncMock(return_value=(
        False, "", mark_moderation("API错误 (HTTP 400): content policy"), (0, 0),
        {"attempts": 2, "failed_tokens": 0}))
    analyzer.prompt_builder = MagicMock()
    analyzer.prompt_builder.build_messages = MagicMock(return_value=[{"role": "user", "content": "x"}])
    result, tokens, retry_info = await analyzer.analyze_chapter(1, "内容", MagicMock())
    assert result is None
    assert "[MODERATION]" in (retry_info.get("error") or "")
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest backend/tests/test_moderation.py -q`
Expected: 新增用例 FAIL（`retry_info` 无 error 键）

- [ ] **Step 3: 实现传播**

`backend/core/analyzer.py` 失败返回处（`if not success:` 分支）：
```python
            if not success:
                logger.error(f"第{chapter_number}章分析失败: {error}")
                # 保留实际消耗的 tokens（API 已扣费，不能归零）
                actual_tokens = token_counts if isinstance(token_counts, tuple) else (0, 0)
                retry_info["error"] = error  # 传播原始错误（含 [MODERATION] 标记，供 pipeline 判 skipped）
                return None, actual_tokens, retry_info
```

`_parse_response` 返回 None 处（`if result is None:`）：
```python
            if result is None:
                logger.error(f"第{chapter_number}章JSON解析失败，LLM响应前800字符:\n{response[:800]}")
                retry_info["error"] = "JSON解析失败"
                return None, (in_tok, out_tok), retry_info
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest backend/tests/test_moderation.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/core/analyzer.py backend/tests/test_moderation.py
git commit -m "feat: analyzer 失败路径传播原始错误串"
```

---

### Task 5: pipeline 跳过逻辑 + 配置项

**Files:**
- Modify: `backend/core/pipeline.py`、`backend/core/memory_state.py`、`backend/config/settings.py`、`frontend/src/api/client.ts`、`frontend/src/pages/SettingsPage.vue`
- Test: `backend/tests/test_moderation.py`（追加）+ `backend/tests/test_config_robustness.py`（配置字段往返）

**Interfaces:**
- Consumes: `is_moderation_error`（Task 1）、`retry_info["error"]`（Task 4）
- Produces: `MemoryState.add_skipped(block_id, reason)` / `state._skipped_chapters: Dict[int, str]`；pipeline 返回 payload 新增 `skipped_chapters: List[str]`；`AnalysisConfig.skip_moderation_blocked: bool = True`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_memory_state_add_skipped():
    """skipped 与 failed 分离：不进失败集，可独立统计"""
    from backend.core.memory_state import MemoryState
    state = MemoryState()
    state.add_failed(1, "普通失败")
    state.add_skipped(3, "内容审核拦截")
    assert 1 in state._failed_chapters
    assert 3 in state._skipped_chapters
    assert 3 not in state._failed_chapters
```

```python
def test_config_skip_moderation_default():
    """配置默认开启 skip_moderation_blocked，且 to_dict/from_dict 往返保留"""
    from backend.config.settings import AppConfig, _filter_fields
    cfg = AppConfig()
    assert cfg.analysis.skip_moderation_blocked is True
    d = cfg.to_dict()
    d["analysis"]["skip_moderation_blocked"] = False
    cfg2 = AppConfig.from_dict(d)
    assert cfg2.analysis.skip_moderation_blocked is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest backend/tests/test_moderation.py backend/tests/test_config_robustness.py -q`
Expected: FAIL（无 add_skipped / 无配置字段）

- [ ] **Step 3: 实现**

`backend/config/settings.py` — `AnalysisConfig` 末尾追加：
```python
    # 内容审核拦截处理：识别为审核拦截的章节重试1次后跳过并标记（不进失败集、不补跑）
    skip_moderation_blocked: bool = True
```

`backend/core/memory_state.py` — `__init__` 追加：
```python
        self._skipped_chapters: Dict[int, str] = {}  # 内容审核拦截跳过的块
```
新增方法（放 `add_failed` 旁）：
```python
    def add_skipped(self, chapter_number: int, reason: str) -> None:
        """记录被内容审核拦截跳过的章节（不进失败集，补跑不重试）"""
        self._skipped_chapters[chapter_number] = reason
```

`backend/core/pipeline.py`：
- import 追加 `from ..core.moderation import is_moderation_error`
- `_analyze_one_block` 失败分支：
```python
        if result is None:
            err_text = (retry_info or {}).get("error", "")
            if (self.config.analysis.skip_moderation_blocked
                    and is_moderation_error(err_text)):
                reason = "内容审核拦截"
                state.add_skipped(block_id, reason)
                logger.warning(f"块{block_id}内容审核拦截，跳过（重试1次后仍拦截）")
                return False, elapsed, ch_tokens, None, retry_info
            reason = "分析失败（LLM响应解析失败或重试耗尽）"
            state.add_failed(block_id, reason)
            logger.warning(f"块{block_id}分析失败: {reason}，耗时{elapsed:.1f}s，tokens={ch_tokens}")
            return False, elapsed, ch_tokens, None, retry_info
```
- `_handle_block_outcome` 失败分支开头（`else:` 内）加 skipped 提示：
```python
        else:
            if retry_info.get("moderation_skip"):
                await self._emit({
                    "chapter": block_id, "status": "skipped",
                    "progress": completed_count, "total": total,
                    "message": f"{prefix}{ch_range} 内容审核拦截，已跳过"
                })
                return False, completed_count
```
  配套：`_analyze_one_block` 的 skipped 返回处设置 `retry_info["moderation_skip"] = True`。
- 完成统计（`result_count` 附近）：
```python
        skipped_chapters = [f"{c}({r})" for c, r in state._skipped_chapters.items()]
```
  并在返回 payload 加 `"skipped_chapters": skipped_chapters`（两个 return 分支都要）。
- 补跑循环不需要改（skipped 不在 `_failed_chapters` 中，天然被跳过）。

`frontend/src/api/client.ts` — `AppConfigDto.analysis` 追加：
```ts
    skip_moderation_blocked: boolean
```

`frontend/src/pages/SettingsPage.vue` — 分析参数卡片加开关（参照 `auto_archive` 写法）：
```html
      <div class="flex items-center gap-2">
        <label class="text-sm flex-1" style="color: var(--color-system-gray)" title="识别为内容审核拦截的章节：重试1次后跳过并标记，不再反复重试白烧成本">跳过内容审核拦截章节:</label>
        <input v-model="config.analysis.skip_moderation_blocked" type="checkbox" />
      </div>
```

`backend/services/queue_service.py` — `_run_one_item` 中 `result.get("failed_chapters")` 分支后追加 skipped 提示：
```python
            elif result.get("skipped_chapters"):
                item.status = "done"  # 有拦截跳过但整体完成
                item.error_message = f"内容审核拦截{len(result['skipped_chapters'])}块"
                await hub.log(
                    f"《{item.name}》完成，{len(result['skipped_chapters'])}块被内容审核拦截跳过: "
                    f"{result['skipped_chapters']}",
                    level="warn")
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest backend/tests/test_moderation.py backend/tests/test_config_robustness.py -q`
Expected: PASS；全量 `python -m pytest -q`（79+新用例全过）；`npm run build`（frontend/，EXIT=0）

- [ ] **Step 5: 提交**

```bash
git add backend/ frontend/src/api/client.ts frontend/src/pages/SettingsPage.vue
git commit -m "feat: 审核拦截章节跳过机制 + skip_moderation_blocked 配置"
```

---

### Task 6: 端到端验证 + 文档

**Files:**
- Test: `backend/tests/test_moderation.py`（追加集成用例，mock analyzer 链路）

- [ ] **Step 1: 写失败测试（追加）**

```python
async def test_pipeline_analyze_block_skips_moderation(tmp_path):
    """_analyze_one_block：analyzer 返回带 [MODERATION] 错误 → add_skipped 而非 add_failed"""
    from backend.core.pipeline import AnalysisPipeline
    from backend.config.settings import AppConfig
    from backend.core.moderation import mark_moderation
    from unittest.mock import MagicMock

    cfg = AppConfig()
    cfg.analysis.block_size = 2
    cfg.analysis.concurrency = 2
    cfg.analysis.skip_moderation_blocked = True

    pipeline = AnalysisPipeline(config=cfg, directory=tmp_path)
    pipeline._block_map = {3: [3, 4]}
    pipeline.file_processor = MagicMock()
    pipeline.file_processor.read_block = AsyncMock(return_value="正文")

    analyzer = MagicMock()
    async def fake_analyze(ch, content, kb, block_size=1):
        return None, (10, 10), {"error": mark_moderation("API错误 (HTTP 400): content policy"), "retries": 2}
    analyzer.analyze_chapter = fake_analyze

    state = pipeline.state
    ok, _elapsed, _tokens, result, retry_info = await pipeline._analyze_one_block(
        analyzer, pipeline.file_processor, state, 3, [3, 4], block_size=2)
    assert not ok
    assert 3 in state._skipped_chapters
    assert 3 not in state._failed_chapters
    assert retry_info.get("moderation_skip") is True


async def test_pipeline_skips_when_config_off(tmp_path):
    """skip_moderation_blocked=False 时：拦截错误按普通失败处理（进 failed、可补跑）"""
    from backend.core.pipeline import AnalysisPipeline
    from backend.config.settings import AppConfig
    from backend.core.moderation import mark_moderation
    from unittest.mock import MagicMock

    cfg = AppConfig()
    cfg.analysis.skip_moderation_blocked = False
    pipeline = AnalysisPipeline(config=cfg, directory=tmp_path)
    pipeline._block_map = {3: [3, 4]}
    pipeline.file_processor = MagicMock()
    pipeline.file_processor.read_block = AsyncMock(return_value="正文")
    analyzer = MagicMock()
    async def fake_analyze(ch, content, kb, block_size=1):
        return None, (10, 10), {"error": mark_moderation("API错误 (HTTP 400): content policy"), "retries": 2}
    analyzer.analyze_chapter = fake_analyze
    state = pipeline.state
    ok, _e, _t, _r, _ri = await pipeline._analyze_one_block(
        analyzer, pipeline.file_processor, state, 3, [3, 4], block_size=2)
    assert not ok
    assert 3 in state._failed_chapters
    assert 3 not in state._skipped_chapters
```

- [ ] **Step 2: 全量回归 + 构建**

Run: `python -m pytest -q` → 全绿；`npm run build`（frontend/）→ EXIT=0

- [ ] **Step 3: 文档与收尾**

`docs/superpowers/plans/2026-08-13-moderation-skip.md` 末尾追加"已实现"记录：
```markdown
## 实现记录（2026-08-13）
- 已完成 Task 1-6。检测层：错误码(MiniMax 1026/1027、OpenAI content_*)、
  中英文关键词(智谱"安全规范"、小米"high risk"等)、隐式信号(content_filter/空内容/refusal)。
- 行为：拦截章节重试1次→仍拦截→标记 ModerationBlocked 短路；pipeline 标 skipped 不补跑；
  完成统计含 skipped_chapters；api_failures.log 有 ModerationBlocked 分类。
- 配置：analysis.skip_moderation_blocked（默认 true）。
```

- [ ] **Step 4: 提交**

```bash
git add docs/superpowers/plans/2026-08-13-moderation-skip.md
git commit -m "docs: 内容审核拦截跳过机制实现记录"
```

## 实现记录（2026-08-13）
- 已完成 Task 1-6。检测层：错误码(MiniMax 1026/1027、OpenAI content_*)、
  中英文关键词(智谱"安全规范"、小米"high risk"等)、隐式信号(content_filter/空内容/refusal)。
- 行为：拦截章节重试1次→仍拦截→标记 ModerationBlocked 短路；pipeline 标 skipped 不补跑；
  完成统计含 skipped_chapters；api_failures.log 有 ModerationBlocked 分类。
- 配置：analysis.skip_moderation_blocked（默认 true），设置页有开关。
- 测试：backend/tests/test_moderation.py 13 个用例（分类器/标记注入/短路/传播/skipped 分流/配置开关）；
  全量 89 passed；npm run build 通过。
- 备注：skipped 不持久化（断点续跑重跑会重新判定，天然自愈）。
