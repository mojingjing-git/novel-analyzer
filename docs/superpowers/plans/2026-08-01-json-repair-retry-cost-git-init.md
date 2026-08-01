# 畸形 JSON 修复 + 重试成本可见 + git 初始化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 LLM 畸形 JSON 能自动修复（兜底增强，不做重试提示改动）；把重试次数/失败 token 成本展示到统计面板；完成项目 git 初始化。

**Architecture:** 后端解析链新增漏引号定向修复策略（json_utils），`safe_parse_json` 委托 `parse_json_robust` 后对既有调用方自动生效；llm_client 增加尝试次数计数器，analyzer→pipeline→queue_service 逐层透传 retry_info 到 token_stats API，前端统计面板展示并去掉运行中门控；git 已初始化完成（验证即可）。

**Tech Stack:** Python 3.13 / FastAPI / Vue 3 + TS / pytest / json5 / json_repair

## Global Constraints

- 项目根：`F:\AI\小说分析器`；后端代码在 `backend/`，前端在 `frontend/`。
- Windows 环境，PowerShell 命令用 `pwsh`；pytest 运行命令：`python -m pytest backend/tests -q`（在项目根执行）。
- 前端类型检查：`npx vue-tsc --noEmit`（在 `frontend/` 执行）。
- 所有新代码遵循现有风格：不加注释之外的装饰性改动；**不引入新依赖**（json5/json_repair 已可用）。
- `safe_parse_json` 的公开签名 `(text) -> Optional[dict]` 不得改变（其他调用方依赖）；新增 `parse_json_robust`，`safe_parse_json` 内部委托，修复链对 analyzer 等既有调用方自动生效。
- 每次任务结束提交 git（身份已配置：novel-analyzer / dev@local，仓库级）。
- **不得提交** config.json、*.log、workspace/、queue_state.json（.gitignore 已排除）。

---

### Task 1: 验证 git 初始化状态（已完成工作的回归确认）

**Files:**
- 验证：`F:\AI\小说分析器\.gitignore`、git 日志

**Interfaces:**
- Consumes: 无
- Produces: 确认仓库可用（后续任务的 commit 前提）

- [ ] **Step 1: 验证仓库状态**

```bash
git log --oneline
git check-ignore config.json workspace analyzer.log queue_state.json
git status --short
```

- [ ] **Step 2: 断言预期输出**

期望：
- `git log --oneline` 有 ≥1 条提交（`1e9eb03 init: ...` 和 `8fe7736 docs: ...`）
- `git check-ignore` 四个路径全部命中（输出自身路径）
- `git status --short` 干净（或仅含未提交的代码改动，无 config.json/日志/workspace）

- [ ] **Step 3: 如果 check-ignore 有未命中项，修正 .gitignore 并提交**

```bash
git add .gitignore
git commit -m "chore: 补充 gitignore 排除项"
```

- [ ] **Step 4: Commit（如 Step 3 有改动才执行）**

---

### Task 2: json_utils 漏引号修复 + parse_json_robust（TDD）

**Files:**
- Modify: `backend/utils/json_utils.py`
- Test: `backend/tests/test_json_repair.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces:
  - `repair_missing_quotes(text: str) -> str`
  - `parse_json_robust(text: str) -> Tuple[Optional[dict], Optional[str]]`
    - 成功直接解析 → `(data, None)`
    - 漏引号修复后成功 → `(data, "已自动修复JSON（漏引号）后解析成功")`
    - 全部失败 → `(None, 首次直接解析的 JSONDecodeError 文本或 "空文本")`
  - `safe_parse_json(text)` 改为内部委托 `parse_json_robust`，签名不变

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_json_repair.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest backend/tests/test_json_repair.py -v
```

期望：FAIL（`ImportError` / `AttributeError: module 'backend.utils.json_utils' has no attribute 'repair_missing_quotes'`）

- [ ] **Step 3: 实现修复**

在 `backend/utils/json_utils.py` 中：

a) 文件顶部（`re` 已导入）新增两个模块级正则与修复函数（放在 `_replace_single_quoted_values` 之后）：

```python
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
```

b) 新增 `parse_json_robust`（完整替换 `safe_parse_json` 的解析链逻辑，原 7 层策略保持顺序不变，末尾追加策略 8）：

```python
def parse_json_robust(text: str):
    """
    安全解析 JSON 并返回诊断信息（LLM 输出容错链 + 漏引号定向修复）

    Returns:
        (dict, None)                    — 直接解析成功
        (dict, "已自动修复JSON（漏引号）后解析成功") — 经漏引号修复成功
        (None, 诊断文本)                 — 全部失败，诊断含首次直接解析错误（含行号）
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    if not text:
        return None, "空文本"

    first_error: Optional[str] = None

    # 策略1: 直接解析
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        first_error = str(e)

    # 策略2: 移除尾部内容（从最后一个完整的}之后）
    last_brace = text.rfind('}')
    if last_brace != -1:
        trimmed = text[:last_brace + 1]
        try:
            return json.loads(trimmed), None
        except json.JSONDecodeError:
            pass

    # 策略3: 移除尾部注释（// 风格）
    base = trimmed if last_brace != -1 else text
    comment_match = re.search(r'(}\s*)//.*$', base, re.MULTILINE | re.DOTALL)
    if comment_match:
        cleaned = base[:comment_match.start()].strip()
        try:
            return json.loads(cleaned), None
        except json.JSONDecodeError:
            pass

    # 策略4: json5 — 处理单引号、尾逗号、注释、Python bool/None
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

    # 策略5: ast.literal_eval — 处理单引号字符串（Python dict 格式）
    _log.info(f"JSON解析进入 ast.literal_eval 策略（文本长度={text_len}）")
    try:
        import ast
        result = ast.literal_eval(text)
        if isinstance(result, dict):
            return result, None
    except Exception as e:
        _log.info(f"ast.literal_eval 解析失败: {e}")

    # 策略6: json_repair
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

    # 策略7: 单引号→双引号替换（保守：只替换键值对中的单引号值）
    try:
        fixed = _replace_single_quoted_values(text)
        if fixed != text:
            return json.loads(fixed), None
    except json.JSONDecodeError:
        pass

    # 策略8: 漏引号定向修复（最多两轮，每轮后重试标准解析与 json5）
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

    return None, first_error or "JSON解析失败（未知原因）"
```

c) `safe_parse_json` 改为委托（保留原 docstring 首行说明，正文替换）：

```python
def safe_parse_json(text: str) -> Optional[dict]:
    """
    安全地解析JSON字符串，支持多种容错策略（LLM输出不完美时的fallback链）

    内部委托 parse_json_robust，仅返回 dict 或 None（兼容既有调用方）。
    """
    data, _ = parse_json_robust(text)
    return data
```

注意：`parse_json_robust` 引用了 `text_len` 与 `Optional`——`Optional` 已在文件顶部导入；`text_len` 需在函数开头定义：

```python
    text_len = len(text)
```

（放在 `first_error` 初始化之后、策略 1 之前。`parse_json_robust` 内其它使用 `text_len` 的位置保持不变。）

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest backend/tests/test_json_repair.py -v
```

期望：PASS（7 个测试全绿）

- [ ] **Step 5: 跑全量测试确认无回归**

```bash
python -m pytest backend/tests -q
```

期望：旧 27 + 新 7 = 34 个全绿

- [ ] **Step 6: Commit**

```bash
git add backend/utils/json_utils.py backend/tests/test_json_repair.py
git commit -m "feat: json 漏引号定向修复 + parse_json_robust 诊断信息"
```

---

### Task 3: llm_client 尝试次数计数器（TDD）

**Files:**
- Modify: `backend/core/llm_client.py`
- Test: `backend/tests/test_llm_mock.py`

**Interfaces:**
- Consumes: 无
- Produces: `LLMClient.get_stats()` 增加 `"total_attempts": int`（累计真实 API 尝试次数，含温度退火与指数退避的每一次）

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_llm_mock.py`（放在 `test_validation_failure_triggers_retry` 之后）：

```python
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
```

并在 `main()` 中追加调用：

```python
    await test_attempts_counter()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest backend/tests/test_llm_mock.py -k attempts -v
```

期望：FAIL（`KeyError: 'total_attempts'`）

- [ ] **Step 3: 实现**

在 `backend/core/llm_client.py`：

a) `__init__` 中 `self._request_count = 0` 之后新增：

```python
        self._attempts = 0
```

b) `chat_with_retry` 温度退火循环中 `total_attempts += 1`（约 line 350）之后新增：

```python
            with self._stats_lock:
                self._attempts += 1
```

c) 指数退避循环中 `total_attempts += 1`（约 line 439）之后新增：

```python
            with self._stats_lock:
                self._attempts += 1
```

d) `get_stats()` 增加：

```python
        return {
            "total_requests": self._request_count,
            "total_attempts": self._attempts,
            "total_tokens": self._total_tokens,
            "failed_tokens": self._failed_tokens,
            "cached_tokens": self._cached_tokens,
            "failure_log_summary": _get_failure_logger().get_summary()
        }
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest backend/tests/test_llm_mock.py -v
```

期望：PASS（6 个测试全绿）

- [ ] **Step 5: 全量回归 + Commit**

```bash
python -m pytest backend/tests -q
git add backend/core/llm_client.py backend/tests/test_llm_mock.py
git commit -m "feat: llm_client 累计 total_attempts 计数器"
```

---

### Task 4: analyzer 3 元组返回 + pipeline/queue_service 透传（TDD）

**Files:**
- Modify: `backend/core/analyzer.py`、`backend/core/pipeline.py`、`backend/services/queue_service.py`
- Test: `backend/tests/test_queue_service.py`（追加）

**Interfaces:**
- Consumes: `get_stats()["total_attempts"]`、`get_stats()["failed_tokens"]`（Task 3）
- Produces:
  - `analyze_chapter(...) -> (result, ch_tokens, retry_info)`；retry_info = `{"retries": int, "failed_tokens": int}`（失败时 result 为 None，retry_info 照常返回）
  - pipeline `_analyze_one_block -> (success, elapsed, ch_tokens, result, retry_info)`
  - pipeline done/failed 事件 payload 含 `retries`、`failed_tokens`
  - `AnalysisService.token_stats()` 含 `total_retries`、`total_failed_tokens`
  - `AnalysisService._record_chapter_stat(payload: dict) -> None`（新方法，供单元测试）

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_queue_service.py`：

```python
def test_record_chapter_stat_accumulates():
    """每章统计含重试成本字段，且总量累加"""
    from backend.services.queue_service import AnalysisService
    svc = object.__new__(AnalysisService)
    svc._chapter_stats = []
    svc._token_stats = {}
    svc._analysis_start_time = 0.0
    svc._runner_task = None
    svc._pipeline = None
    svc._stop_requested = False
    svc._total_retries = 0
    svc._total_failed_tokens = 0

    svc._record_chapter_stat({
        "chapter": 1, "elapsed": 1.0,
        "input_tokens": 10, "output_tokens": 20,
        "retries": 1, "failed_tokens": 30,
    })
    svc._record_chapter_stat({
        "chapter": 2, "elapsed": 2.0,
        "input_tokens": 5, "output_tokens": 5,
        "retries": 0, "failed_tokens": 0,
    })

    stats = svc.token_stats()
    assert stats["total_retries"] == 1
    assert stats["total_failed_tokens"] == 30
    assert stats["chapter_stats"][0]["retries"] == 1
    assert stats["chapter_stats"][0]["failed_tokens"] == 30
    assert stats["chapter_stats"][0]["input_tokens"] == 10
```

并在 `__main__` 块追加：

```python
    test_record_chapter_stat_accumulates()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest backend/tests/test_queue_service.py -k record_chapter_stat -v
```

期望：FAIL（`AttributeError: 'AnalysisService' object has no attribute '_record_chapter_stat'`）

- [ ] **Step 3: 实现 analyzer 返回 3 元组**

在 `backend/core/analyzer.py` 的 `analyze_chapter` 中：

a) 调用 `chat_with_retry` 前后计算差值（替换原调用块）：

```python
            # 2. 调用LLM（带重试和温度退火，包含JSON解析验证）
            stats_before = self.llm_client.get_stats()
            success, response, error, token_counts = await self.llm_client.chat_with_retry(
                messages,
                max_tokens=self.config.api.max_tokens,
                validate_response=validate_json_response,
                retry_messages_builder=build_retry_messages
            )
            stats_after = self.llm_client.get_stats()
            retry_info = {
                "retries": max(0, stats_after.get("total_attempts", 0) - stats_before.get("total_attempts", 0) - 1),
                "failed_tokens": max(0, stats_after.get("failed_tokens", 0) - stats_before.get("failed_tokens", 0)),
            }
```

b) 所有 return 改为 3 元组：

```python
        if self._is_stopped:
            logger.info("分析已被停止")
            return None, (0, 0), {"retries": 0, "failed_tokens": 0}
```

```python
            if not success:
                logger.error(f"第{chapter_number}章分析失败: {error}")
                # 保留实际消耗的 tokens（API 已扣费，不能归零）
                actual_tokens = token_counts if isinstance(token_counts, tuple) else (0, 0)
                return None, actual_tokens, retry_info
```

```python
            result = self._parse_response(response, chapter_number, parsed_cache[0])
            if result is None:
                logger.error(f"第{chapter_number}章JSON解析失败，LLM响应前800字符:\n{response[:800]}")
                return None, (in_tok, out_tok), retry_info
```

```python
            logger.info(f"第{chapter_number}章分析完成")
            return result, (in_tok, out_tok), retry_info
```

```python
        except Exception as e:
            logger.error(f"第{chapter_number}章分析异常: {e}", exc_info=True)
            # 尽量保留已消耗的 tokens
            return None, (0, 0), {"retries": 0, "failed_tokens": 0}
```

c) docstring 返回值说明更新为：

```python
        Returns:
            (分析结果, 本章消耗token数, {retries, failed_tokens})，
            失败返回 (None, 已消耗tokens, retry_info)
```

- [ ] **Step 4: 实现 pipeline 透传**

在 `backend/core/pipeline.py`：

a) `_analyze_one_block`（约 line 555-586）：

- docstring `Returns:` 改为 `(success, elapsed, ch_tokens, result, retry_info)`
- 读取失败分支：

```python
        content = await asyncio.to_thread(file_processor.read_block, block_id, block_size)
        if content is None:
            state.add_failed(block_id, "读取失败")
            return False, 0.0, (0, 0), None, {"retries": 0, "failed_tokens": 0}
```

- 调用与返回：

```python
        result, ch_tokens, retry_info = await analyzer.analyze_chapter(
            block_id, content, temp_kb, block_size=block_size)
```

```python
        if result is None:
            reason = "分析失败（LLM响应解析失败或重试耗尽）"
            state.add_failed(block_id, reason)
            logger.warning(f"块{block_id}分析失败: {reason}，耗时{elapsed:.1f}s，tokens={ch_tokens}")
            return False, elapsed, ch_tokens, None, retry_info

        await state.add_result(result)
        return True, elapsed, ch_tokens, result, retry_info
```

b) `analyze_block_with_progress`（约 line 588-623）：

- 提前停止分支：

```python
        if self._stop_requested:
            return False, completed_count, (0, 0), None, {"retries": 0, "failed_tokens": 0}
```

- 解包与传递：

```python
        success, elapsed, ch_tokens, result, retry_info = await self._analyze_one_block(
            self._analyzer, self.file_processor, self.state, block_id, block_size
        )

        success, new_completed = await self._handle_block_outcome(
            success=success, block_id=block_id, block_chs=block_chs,
            block_size=block_size, total=total, label=label,
            completed_count=completed_count, elapsed=elapsed,
            ch_tokens=ch_tokens, result=result, retry_info=retry_info,
        )
        return success, new_completed, ch_tokens, result, retry_info
```

- docstring `Returns:` 改为 `(success, new_completed_count, ch_tokens, result, retry_info)`

c) 预热调用处（约 line 358）解包改 5 元组：

```python
                _, completed_count, _, _, _ = await self.analyze_block_with_progress(
```

d) `_worker`（约 line 415-421）与消费处（约 line 432）：

```python
        async def _worker(block_id, block_chs):
            async with sem:
                if self._stop_requested:
                    return block_id, block_chs, False, 0.0, (0, 0), None, {"retries": 0, "failed_tokens": 0}, True
                success, elapsed, ch_tokens, result, retry_info = await self._analyze_one_block(
                    analyzer, self.file_processor, state, block_id, block_size)
                return block_id, block_chs, success, elapsed, ch_tokens, result, retry_info, False
```

```python
                block_id, block_chs, success, elapsed, ch_tokens, result, retry_info, skipped = await fut
                if skipped:
                    continue
                _, completed_count = await self._handle_block_outcome(
                    success=success, block_id=block_id, block_chs=block_chs,
                    block_size=block_size, total=total, label="",
                    completed_count=completed_count, elapsed=elapsed,
                    ch_tokens=ch_tokens, result=result, retry_info=retry_info,
                )
```

e) `_handle_block_outcome`（约 line 625-670）：

- 签名与默认值：

```python
    async def _handle_block_outcome(
        self, *, success: bool, block_id: int, block_chs, block_size: int,
        total: int, label: str, completed_count: int, elapsed: float,
        ch_tokens, result, retry_info: Optional[dict] = None,
    ):
```

- 函数体开头归一化：

```python
        retry_info = retry_info or {"retries": 0, "failed_tokens": 0}
```

- done payload 增加：

```python
            await self._emit({
                "chapter": block_id, "status": "done",
                "result": result_dict,
                "progress": completed_count, "total": total, "elapsed": elapsed,
                "tokens": ch_tokens[0] + ch_tokens[1],
                "input_tokens": ch_tokens[0], "output_tokens": ch_tokens[1],
                "retries": retry_info.get("retries", 0),
                "failed_tokens": retry_info.get("failed_tokens", 0),
                "message": f"{prefix}{ch_range}分析完成"
            })
```

- failed payload 增加：

```python
            await self._emit({
                "chapter": block_id, "status": "failed",
                "progress": completed_count, "total": total,
                "retries": retry_info.get("retries", 0),
                "failed_tokens": retry_info.get("failed_tokens", 0),
                "message": f"{prefix}{ch_range}分析失败"
            })
```

- [ ] **Step 5: 实现 queue_service 统计**

在 `backend/services/queue_service.py`：

a) `__init__` 中（`self._chapter_stats: List[Dict[str, Any]] = []` 之后）：

```python
        self._total_retries = 0
        self._total_failed_tokens = 0
```

b) `start()` 中（`self._chapter_stats = []` 之后）：

```python
        self._total_retries = 0
        self._total_failed_tokens = 0
```

c) `token_stats()` 返回增加：

```python
        return {
            "categories": self._token_stats,
            "chapter_stats": self._chapter_stats[-200:],  # 最近200章
            "elapsed": elapsed,
            "running": self.is_running,
            "total_retries": self._total_retries,
            "total_failed_tokens": self._total_failed_tokens,
        }
```

d) 新增方法（放在 `status()` 之后）：

```python
    def _record_chapter_stat(self, payload: dict) -> None:
        """记录每章统计（含重试成本），供 /api/analysis/token_stats 展示"""
        self._chapter_stats.append({
            "chapter": payload.get("chapter", 0),
            "elapsed": payload.get("elapsed", 0),
            "input_tokens": payload.get("input_tokens", 0),
            "output_tokens": payload.get("output_tokens", 0),
            "retries": payload.get("retries", 0),
            "failed_tokens": payload.get("failed_tokens", 0),
        })
        self._total_retries += payload.get("retries", 0) or 0
        self._total_failed_tokens += payload.get("failed_tokens", 0) or 0
```

e) `on_progress` 中替换（约 line 517-524）：

```python
                # 记录每章统计
                if status == "done":
                    self._record_chapter_stat(payload)
```

- [ ] **Step 6: 运行测试确认通过**

```bash
python -m pytest backend/tests/test_queue_service.py -v
```

期望：PASS（8 个测试全绿）

- [ ] **Step 7: 全量回归 + Commit**

```bash
python -m pytest backend/tests -q
git add backend/core/analyzer.py backend/core/pipeline.py backend/services/queue_service.py backend/tests/test_queue_service.py
git commit -m "feat: 重试成本统计透传（analyzer→pipeline→token_stats）"
```

---

### Task 5: 前端统计面板展示重试成本

**Files:**
- Modify: `frontend/src/api/client.ts`、`frontend/src/pages/StatsPage.vue`

**Interfaces:**
- Consumes: `token_stats` API 新字段（Task 4）
- Produces: 统计面板重试成本可视化

- [ ] **Step 1: 扩展类型定义**

`frontend/src/api/client.ts`：

```ts
export interface ChapterStat {
  chapter: number
  elapsed: number
  input_tokens: number
  output_tokens: number
  retries?: number
  failed_tokens?: number
}

export interface TokenStatsResponse {
  categories: Record<string, TokenCategory>
  chapter_stats: ChapterStat[]
  elapsed: number
  running: boolean
  total_retries?: number
  total_failed_tokens?: number
}
```

- [ ] **Step 2: 修改 StatsPage.vue script**

`frontend/src/pages/StatsPage.vue`：

a) `refresh()` 去掉运行中门控（始终取数，结束后保留统计）：

```ts
async function refresh() {
  try {
    // 运行状态与统计分离：结束后也能查看本次完整统计
    const status = await api.analysisStatus()
    running.value = status?.running ?? false
    stats.value = await api.getTokenStats()
  } catch (e) { console.error(e) }
}
```

b) 新增 computed（`totalOutputTokens` 之后）：

```ts
const totalRetries = computed(() => stats.value?.total_retries || 0)
const totalFailedTokens = computed(() => stats.value?.total_failed_tokens || 0)
```

- [ ] **Step 3: 修改 StatsPage.vue template**

a) 标题行加运行状态徽标：

```html
<h2 class="section-title">
  统计面板
  <span
    class="ml-2 px-2 py-0.5 rounded-full text-xs align-middle"
    :class="running ? 'bg-green-100 text-green-600' : 'bg-gray-100 text-gray-500'"
  >{{ running ? '运行中' : '已结束' }}</span>
</h2>
```

b) 卡片区 `grid-cols-4` 改为 `grid-cols-3`，并在现有 4 卡后追加 2 卡：

```html
<div class="bg-white border border-gray-200 rounded-lg p-3 text-center"><div class="text-xl font-bold text-amber-500">{{ totalRetries }}</div><div class="text-xs" style="color: var(--color-system-gray)">重试次数</div></div>
<div class="bg-white border border-gray-200 rounded-lg p-3 text-center"><div class="text-xl font-bold text-red-500">{{ totalFailedTokens.toLocaleString() }}</div><div class="text-xs" style="color: var(--color-system-gray)">失败Tokens·已扣费</div></div>
```

c) 每章明细表加两列（表头在 `<th>输出Tokens</th>` 后、`<th>t/s</th>` 前）：

```html
<th class="px-2 py-1 text-left">重试</th>
<th class="px-2 py-1 text-left">失败Token</th>
```

d) 对应单元格（`<td>{{ s.output_tokens }}</td>` 后、t/s 单元格前）：

```html
<td class="px-2 py-1">{{ s.retries ?? 0 }}</td>
<td class="px-2 py-1">{{ (s.failed_tokens ?? 0).toLocaleString() }}</td>
```

- [ ] **Step 4: 类型检查**

```bash
npx vue-tsc --noEmit
```

期望：零错误

- [ ] **Step 5: 手动验证清单（告知用户）**

- 启动后端 + 前端，运行中打开统计面板：看到「重试次数」「失败Tokens·已扣费」卡片与徽标「运行中」
- 停止分析后：面板仍显示本次统计，徽标变「已结束」
- 每章明细表出现「重试」「失败Token」列

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/pages/StatsPage.vue
git commit -m "feat: 统计面板展示重试次数与失败token成本"
```

---

### Task 6: 集成验证与收尾

**Files:**
- 验证全部改动

- [ ] **Step 1: 全量后端测试**

```bash
python -m pytest backend/tests -q
```

期望：全绿（旧 27 + 新 9 = 36）

- [ ] **Step 2: 前端类型检查**

```bash
npx vue-tsc --noEmit
```

期望：零错误

- [ ] **Step 3: 语法编译检查**

```bash
python -m py_compile backend/core/llm_client.py backend/core/analyzer.py backend/core/pipeline.py backend/services/queue_service.py backend/utils/json_utils.py
```

期望：无输出（成功）

- [ ] **Step 4: 仓库卫生检查**

```bash
git status --short
git check-ignore config.json
```

期望：无 config.json/日志/workspace 入库；工作区仅含已提交改动（干净）

- [ ] **Step 5: 提交收尾（如有遗漏改动）**

```bash
git add -A
git commit -m "chore: 集成验证收尾" 2>$null
```

- [ ] **Step 6: 告知用户重启后端使改动生效**

---

## 计划自查记录

- **Spec 覆盖**：① 畸形 JSON（Task 2 修复链，经 `safe_parse_json` 委托全局生效；重试提示增强已按用户决定取消）✓；② 重试成本（Task 3 计数器 → Task 4 透传 → Task 5 展示 + 结束后可见）✓；③ git（Task 1 验证，已初始化）✓
- **占位符扫描**：无 TBD/TODO；每个代码步骤含完整代码
- **类型一致性**：`parse_json_robust`/`repair_missing_quotes`/`_record_chapter_stat` 在定义处与消费处签名一致；`_analyze_one_block` 5 元组、`_handle_block_outcome` 的 `retry_info` kwarg 在所有调用点（预热/worker）一致
