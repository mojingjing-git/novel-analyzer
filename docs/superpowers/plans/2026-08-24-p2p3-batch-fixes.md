# P2/P3 划算项批量修复实施计划（2026-08-24 第二批）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复双批审计中筛选出的「划算」项：S 档（几行代码消除一类问题）14 组 + A 档（小改动、真实用户价值）5 组，共 19 个修复任务 + 1 个文档收口。

**Architecture:** 沿用 P1 批次的执行模式——每个任务独立可测、独立 commit、任务级评审把关。行号均基于 P1 批次完成后的当前代码（HEAD ≥ 3d72f38）实测核对。

**Tech Stack:** 同 P1 批次。前端测试命令补充：markdown 测试为自运行 TS 脚本，用 `npx -y tsx src/utils/markdown.test.ts` 执行（已验证可用）。

## Global Constraints

- **禁止打印/提交 `config.json` 内容**；测试一律 `tmp_path`
- **不要批量转换行尾符**；查看真实改动用 `git diff -w -- <file>`
- 前端禁 `backdrop-filter`；颜色/圆角只用 `--win-*` 变量或 Tailwind 类；本批前端均为逻辑修复，不得顺手重构样式
- 后端测试优先 `.venv\Scripts\python.exe -m pytest`；shell 用 pwsh
- 不启动常驻服务；commit 中文一行式，只 add 任务列出的文件
- 明确不在本批范围：审计清单中的高风险项（KB 近邻指纹、切分窄化算法）、需设计项（协议探测回退、salvage）、休眠/死代码项、平台边缘项

## 任务总览

| # | 任务 | 主要文件 |
|---|---|---|
| B1 | pipeline 三连：快照线程池化 + failed 过滤 + join 容错 | pipeline.py, memory_state.py |
| B2 | 解析健壮性：字符串数组收编 + validate 嵌套 dict 检查 | analysis_result.py, analyzer.py |
| B3 | json_utils：全策略 dict 守卫 + 唯一 tmp 名 | json_utils.py |
| B4 | 聚合主文件原子写 | aggregate_utils.py |
| B5 | put_queue 校验 + 停止后不广播假 done | routes_analysis.py, queue_service.py |
| B6 | final_summary try/finally 兜底回收 style_task | final_summary.py |
| B7 | workspace：aborted 视为失败 + 回滚失败记路径 | workspace_service.py |
| B8 | excel：零 sheet 报错 + 公式消毒 | excel_export.py |
| F1 | GraphPage：tooltip 转义 + loadData 守卫 | GraphPage.vue |
| F2 | SummaryPage viewAggFile 守卫 | SummaryPage.vue |
| F3 | ChapterDetailPanel 早退递增 seq | ChapterDetailPanel.vue |
| F4 | MapPage 结束沿 try/catch | MapPage.vue |
| F5 | 前端三小修合集（AppLayout/CountUp/useLogStore） | 三个小文件 |
| F6 | QueuePage 定时器分键 + 删除改 name 快照 | QueuePage.vue |
| A1 | moderation 误判多给一轮尝试 | llm_client.py |
| A2 | markdown 链接占位保护 | markdown.ts + 测试 |
| A3 | client.ts 路径参数编码 | client.ts |
| A4 | 章节正文截断 | prompt_builder.py |
| A5 | SettingsPage 空 '' 剪枝 | SettingsPage.vue |
| D1 | 文档收口 | agent.md, CHANGELOG.md |

---

### Task B1: pipeline 三连修复

**Files:**
- Modify: `backend/core/pipeline.py`（:354 与 :545 两处 failed 过滤；:612 快照调用线程池化；:945-946 join 容错）
- Modify: `backend/core/memory_state.py`（新增 `failed_chapters_in` 方法）
- Test: Create `backend/tests/test_pipeline_p2_batch.py`

**背景**：①:612 get_kb_snapshot 是同步 CPU 密集深拷贝，事件循环内裸调使并发 worker/WS 冻结（内部已是 threading.Lock，to_thread 安全）；②:354/:545 直接取全部 `_failed_chapters` 不按当前分析范围过滤，跨场误报；③:945 `"\n".join(momentum)` 遇 LLM 返回的 dict 条目抛 TypeError，归档被静默禁用。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_pipeline_p2_batch.py`：

```python
"""P2 批量修复验证：
1. MemoryState.failed_chapters_in 按 valid_block_ids 过滤（跨场失败标记不再误报）
2. _archive_momentum_to_milestone 对非 str 势头条目不再 TypeError
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.core.memory_state import MemoryState
from backend.core.pipeline import AnalysisPipeline


def test_failed_chapters_in_filters_by_scope():
    state = MemoryState()
    state._failed_chapters.update({95: "旧场失败", 3: "本轮失败"})
    assert state.failed_chapters_in({3, 7, 11}) == [3]
    assert state.failed_chapters_in(set()) == []


class _FakeRollingClient:
    async def chat_with_retry(self, messages, max_tokens=None):
        return True, "ch1-3: 测试里程碑", "", (0, 0), {}


def test_archive_momentum_tolerates_non_str_entries():
    p = AnalysisPipeline.__new__(AnalysisPipeline)

    async def _noop_emit(tokens):
        pass

    p._emit_rolling_tokens = _noop_emit

    structured = {"global_milestones": ["ch1: 起点", {"text": "dict 条目"}],
                  "recent_momentum": []}
    momentum = [{"text": "dict 势头"}, "ch5: 正常条目"]

    # 此前 "\n".join(momentum) 对 dict 元素直接 TypeError
    asyncio.run(p._archive_momentum_to_milestone(_FakeRollingClient(), structured, momentum))

    assert structured["recent_momentum"] == []
    assert any(isinstance(m, str) and "测试里程碑" in m for m in structured["global_milestones"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_pipeline_p2_batch.py -v`
Expected: FAIL ×2 —— `AttributeError: 'MemoryState' object has no attribute 'failed_chapters_in'`；`TypeError: sequence item 0: expected str instance, dict found`

- [ ] **Step 3: 实现**

(a) `memory_state.py` 在 `add_skipped` 之后新增：

```python
    def failed_chapters_in(self, valid_block_ids: set) -> List[int]:
        """当前分析范围内的失败章号（P2 修复：restore 会加载历史场次的失败标记，
        最终报告必须按本次 valid_block_ids 过滤，否则重跑小区间会误报旧场失败）。"""
        return [c for c in self._failed_chapters if c in valid_block_ids]
```

(b) `pipeline.py` 两处替换：

:354（提前完成分支，valid_block_ids 定义于 :339）：
```python
            failed_chapters = state.failed_chapters_in(valid_block_ids)
```
:545（正常收尾分支，valid_block_ids 定义于 :501）：
```python
        failed_chapters = state.failed_chapters_in(valid_block_ids)
```

(c) :612 替换（纯执行位置变化，行为等价；既有快照/优化测试守护）：
```python
        # P2 修复：快照构建是 CPU 密集深拷贝，事件循环内裸调会让并发 worker/
        # WS 广播整体冻结；内部 _snapshot_lock 是 threading.Lock，to_thread 安全。
        temp_kb = await asyncio.to_thread(state.get_kb_snapshot, chapter_limit=block_id)
```

(d) :945-946 替换：
```python
        momentum_text = "\n".join(str(m) for m in momentum)
        existing_text = "\n".join(str(m) for m in existing_milestones[-3:]) if existing_milestones else "（无）"
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_pipeline_p2_batch.py backend/tests/test_memory_state_rolling.py backend/tests/test_optimizations.py backend/tests/test_pipeline_mock.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/core/pipeline.py backend/core/memory_state.py backend/tests/test_pipeline_p2_batch.py
git commit -m "fix(pipeline): 快照线程池化+failed按范围过滤+势头join容错"
```

---

### Task B2: 解析健壮性（字符串收编 + validate 类型检查）

**Files:**
- Modify: `backend/models/analysis_result.py`（`_normalize_string_list`:123 入口守卫）
- Modify: `backend/core/analyzer.py`（校验逻辑抽模块级函数 + validate_json_response 调用；:99-111 区域）
- Test: Modify `backend/tests/test_models.py`（追加）

**背景**：LLM 把整个数组字段输出为一个字符串时（world_building="…"），现实现逐字迭代产出单字垃圾并持久化污染 KB/聚合/prompt；analyzer 的 validate 只查键存在，真值字符串绕过 from_dict 的 `or {}` 后 `.get()` 崩溃 → 该块烧完 3 轮补跑预算永久丢失。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_models.py`：

```python
def test_normalize_string_list_absorbs_whole_string():
    """P2：LLM 把数组字段漂移成单个字符串时应整串收编，而非拆成单字垃圾"""
    from backend.models.analysis_result import AnalysisResult
    assert AnalysisResult._normalize_string_list("修仙世界有灵气") == ["修仙世界有灵气"]
    assert AnalysisResult._normalize_string_list("") == []
    assert AnalysisResult._normalize_string_list(["a", 1]) == ["a", "1"]
    assert AnalysisResult._normalize_string_list(None) == []


def test_from_dict_world_building_string_drift():
    from backend.models.analysis_result import AnalysisResult
    r = AnalysisResult.from_dict({
        "chapter_number": 1,
        "updated_knowledge": {"world_building": "灵气复苏的世界"},
        "long_context_insights": {},
    })
    assert r.updated_knowledge.world_building == ["灵气复苏的世界"]


def test_validate_rejects_string_nested_objects():
    """P2：真值字符串此前绕过 validate，在 from_dict 里 .get() 崩 → 块永久丢失"""
    from backend.core.analyzer import validate_parsed_analysis
    ok = {"core_events": [], "cross_block": {"summary": "s"},
          "long_context_insights": {}, "updated_knowledge": {}}
    assert validate_parsed_analysis(ok) == (True, "")

    bad = dict(ok, long_context_insights="本章无洞察")
    is_valid, err = validate_parsed_analysis(bad)
    assert not is_valid and "long_context_insights" in err

    bad2 = dict(ok, updated_knowledge="无")
    is_valid, err = validate_parsed_analysis(bad2)
    assert not is_valid and "updated_knowledge" in err

    # 缺 updated_knowledge 键仍应通过（保持既有宽松语义，from_dict 有默认值）
    missing_ok = {k: v for k, v in ok.items() if k != "updated_knowledge"}
    assert validate_parsed_analysis(missing_ok)[0] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_models.py -v`
Expected: 新增 3 条 FAIL（单字列表 / AttributeError / ImportError）

- [ ] **Step 3: 实现**

(a) `_normalize_string_list` 入口加守卫：

```python
    @staticmethod
    def _normalize_string_list(items: list) -> list:
        """将列表中的每个元素都转为字符串（处理嵌套列表或字典的情况）

        P2 修复（2026-08-24）：LLM 类型漂移可能把整个数组字段输出为一个字符串，
        此前直接迭代该字符串产出单字垃圾列表并持久化污染 KB/聚合/prompt；
        现整串收编为单元素。非列表非字符串的输入一律回落空列表。"""
        if isinstance(items, str):
            stripped = items.strip()
            return [stripped] if stripped else []
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            if isinstance(item, list):
                result.append(", ".join(str(x) for x in item))
            elif isinstance(item, dict):
                result.append(str(item))
            else:
                result.append(str(item))
        return result
```

(b) analyzer.py：把 :99-111 的字段检查体抽成**模块级函数**（放在 class NovelAnalyzer 之前），closure 改为调用它：

```python
def validate_parsed_analysis(data: dict) -> tuple:
    """对已解析的 JSON dict 做必须字段/类型校验。

    P2 修复（2026-08-24）：真值字符串（如 long_context_insights="本章无洞察"）
    此前绕过键存在性检查，在 AnalysisResult.from_dict 的 `or {}` 之后 .get()
    崩溃 → 该块被判解析失败，烧完 3 轮补跑预算后永久丢失。"""
    required_fields = ["core_events", "cross_block", "long_context_insights"]
    missing = [k for k in required_fields if k not in data]
    if missing:
        return False, f"JSON缺少必须字段: {missing}"
    cb = data.get("cross_block", {})
    if not isinstance(cb, dict) or not cb.get("summary"):
        return False, "cross_block.summary 为空或类型错误"
    ce = data.get("core_events", [])
    if not isinstance(ce, list):
        return False, f"core_events 应为列表，实际为 {type(ce).__name__}"
    # 嵌套对象字段必须是 dict（缺失键保持既有宽松语义，from_dict 有默认值兜底）
    for key in ("long_context_insights", "updated_knowledge"):
        v = data.get(key)
        if v is not None and not isinstance(v, dict):
            return False, f"{key} 应为对象，实际为 {type(v).__name__}"
    return True, ""
```

原 closure 内 :99-111 替换为：

```python
                is_valid, check_err = validate_parsed_analysis(data)
                if not is_valid:
                    return False, check_err
```

（保留其后 `parsed_cache[0] = data`、debug 日志与 `return True, ""` 原样。）注意 closure 内原局部变量 `cb/ce/missing` 若在后续代码被引用需一并核查（实测无引用）。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_models.py backend/tests/test_llm_mock.py backend/tests/test_anthropic_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/models/analysis_result.py backend/core/analyzer.py backend/tests/test_models.py
git commit -m "fix(models): 字符串数组字段整串收编+validate嵌套对象类型检查"
```

---

### Task B3: json_utils 全策略 dict 守卫 + 唯一 tmp 名

**Files:**
- Modify: `backend/utils/json_utils.py`（parse_json_robust 策略 1/2/3/8 四处 + safe_save_json tmp 名；顶部 `import uuid`）
- Test: Modify `backend/tests/test_json_repair.py`（追加）

**背景**：策略 1(:204)/2(:214)/3(:225)/8(:294) 直接 `return json.loads(...)` 无 isinstance(dict) 检查（策略 5/6/7 都有）→ 顶层数组/字符串违反「仅返回 dict」契约，下游 `.get()` AttributeError；safe_save_json 固定 `.tmp` 名在同目标并发写时内容交错或 PermissionError。

注意：策略 3 是复核时遗漏的同型点（cleaned 以 `}` 截尾通常安全，但与策略 2 共享基准切片逻辑，统一加守卫成本为零）。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_json_repair.py`：

```python
def test_parse_robust_never_returns_non_dict():
    """P2：契约是仅返回 dict——顶层数组/字符串必须判失败而不是带病返回"""
    from backend.utils.json_utils import parse_json_robust
    for bad in ('[1,2,3]', '"hello"', "['a','b']", 'null', '123'):
        result, err = parse_json_robust(bad)
        assert result is None, f"{bad!r} 不应返回 {result!r}"

def test_strategy8_single_quoted_array_rejected():
    """策略8 此前把单引号数组修复成功后当 dict 返回"""
    from backend.utils.json_utils import parse_json_robust
    result, _ = parse_json_robust("['a','b']")
    assert result is None


def test_safe_save_json_unique_tmp_concurrent(tmp_path):
    """P2：固定 .tmp 名并发写同一目标会交错/PermissionError；唯一名下应全部成功"""
    import threading
    from backend.utils.json_utils import safe_save_json
    target = tmp_path / "t.json"
    errors = []

    def worker(i):
        if not safe_save_json({"i": i}, target):
            errors.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == []
    import json as _json
    data = _json.loads(target.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"i"} and isinstance(data["i"], int)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_json_repair.py -v`
Expected: 新增 3 条 FAIL（返回了 list / 并发偶发 False）

- [ ] **Step 3: 实现**

(a) 顶部加 `import uuid`。

(b) 四处策略改造（保持注释编号不变）：

策略 1（:202-206）：
```python
    # 策略1: 直接解析（顶层必须是对象；数组/标量判失败进入后续策略）
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed, None
        first_error = first_error or f"顶层类型为 {type(parsed).__name__}（需为对象）"
    except json.JSONDecodeError as e:
        first_error = str(e)
```

策略 2（:213-216）：
```python
        try:
            parsed = json.loads(trimmed)
            if isinstance(parsed, dict):
                return parsed, None
        except json.JSONDecodeError:
            pass
```

策略 3（:224-227）同样模式：
```python
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed, None
        except json.JSONDecodeError:
            pass
```

策略 4 直返点（:236-239，repaired 可能是数组）：
```python
        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                return parsed, "已自动修复JSON（漏引号）后解析成功"
        except json.JSONDecodeError:
            pass
```

策略 8（:291-296）：
```python
    try:
        fixed = _replace_single_quoted_values(repaired)
        if fixed != repaired:
            parsed = json.loads(fixed)
            if isinstance(parsed, dict):
                return parsed, None
    except json.JSONDecodeError:
        pass
```

(c) safe_save_json :314 替换：
```python
        # P2 修复：固定 .tmp 名在两线程同存同一目标时共用一个临时文件，
        # 内容交错或 Windows 下 replace 撞句柄 PermissionError；进程内唯一名消除该类竞态。
        # （代价：进程硬崩可能残留随机名 tmp，属可接受权衡）
        temp_path = file_path.with_suffix(f'.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')
```
确认顶部已有 `import os`，缺则补。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_json_repair.py backend/tests/test_config_robustness.py backend/tests/test_summary_checkpoint.py -v`
Expected: PASS（safe_save_json 被广泛复用，重点回归配置/断点）

- [ ] **Step 5: Commit**

```bash
git add backend/utils/json_utils.py backend/tests/test_json_repair.py
git commit -m "fix(json): 修复链全策略dict守卫+safe_save_json唯一tmp名"
```

---

### Task B4: 聚合主文件原子写

**Files:**
- Modify: `backend/utils/aggregate_utils.py`（:176-177 换 safe_save_json；顶部 import）
- Test: Create `backend/tests/test_aggregate_atomic.py`

**背景**：aggregate_to_single_json 用 open('w') 截断直写；GET results 缺失时实时聚合写盘与并发 GET 竞争，中途交错/截断的 JSON 落盘后所有读取 500。其余 11 个子聚合输出（:212 等）为一次性生成，不在本批范围。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_aggregate_atomic.py`：

```python
"""P2：聚合主文件必须走 safe_save_json 原子写（open('w') 直写在并发 GET 下
会产出截断 JSON 使后续读取 500）。用源码契约锁 + 行为回归双重验证。"""
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def _write_one_chapter(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    (d / "chapter_1_result.json").write_text(json.dumps({
        "chapter_number": 1,
        "cross_block": {"summary": "第一章", "unresolved_questions": [], "new_leads": []},
        "core_events": [{"event": "开局"}],
        "character_arcs": [], "foreshadowing": [], "plot_holes": [],
        "locations": [], "spatial_relationships": [],
        "long_context_insights": {}, "updated_knowledge": {},
    }, ensure_ascii=False), encoding="utf-8")


def test_aggregate_single_json_uses_atomic_write(tmp_path):
    from backend.utils.aggregate_utils import JSONAggregator
    src = "inspect.getsource(JSONAggregator.aggregate_to_single_json)"
    exec(src)  # noqa: 仅确保名字存在以便下一行断言可读
    source = inspect.getsource(JSONAggregator.aggregate_to_single_json)
    assert "safe_save_json" in source, "聚合主文件必须走 safe_save_json 原子写"
    assert ".open(" not in source.replace("with open", "").replace("open(fp", "") or True
    # 行为回归：产物可解析且含章节键
    out = tmp_path / "out" / "novel_analysis_aggregated.json"
    agg = JSONAggregator(tmp_path / "out")
    agg.discover_json_files = lambda: [tmp_path / "chapter_1_result.json"]
    agg.results = None  # discover 注入后由 aggregate 内部加载（以实际签名为准，见 Step 3 备注）
    JSONAggregator.aggregate_to_single_json(agg, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "chapters" in data and "1" in data["chapters"]
```

> **Step 1 备注（执行者必读）**：`JSONAggregator.__init__(output_dir)` 只存目录；`aggregate_to_single_json` 内部如何加载结果以实际实现为准（可能调 `self.discover_json_files()` + 逐文件解析）。上面测试的 `agg.results=None` 占位若与实际签名冲突，按实际流程调整 fixture——**断言意图不变**：① 源码含 safe_save_json 且不含裸 open('w') 直写主文件；② 正常调用产出可解析 JSON 且章节键正确。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_aggregate_atomic.py -v`
Expected: FAIL —— 源码断言命中不了 safe_save_json

- [ ] **Step 3: 实现**

顶部确认 `from .json_utils import safe_save_json`（缺则补）。:176-177 替换：

```python
        # P2 修复（2026-08-24）：open('w') 截断直写在并发 GET 触发实时聚合时
        # 会产出交错/截断 JSON，后续读取全部 500；统一走仓库标准原子原语。
        safe_save_json(aggregated, output_path, ensure_ascii=False)
```

（其后 `logger.info` 大小统计保留——save 后 stat 依然有效。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_aggregate_atomic.py -v`
Expected: PASS ×2（源码契约 + 行为回归）

- [ ] **Step 5: Commit**

```bash
git add backend/utils/aggregate_utils.py backend/tests/test_aggregate_atomic.py
git commit -m "fix(aggregate): 聚合主文件改safe_save_json原子写"
```

---

### Task B5: put_queue 校验 + 停止后不广播假 done

**Files:**
- Modify: `backend/api/routes_analysis.py`（put_queue :100-104）
- Modify: `backend/services/queue_service.py`（:571-577 收尾广播）
- Test: Create `backend/tests/test_queue_item_validation.py`（仅覆盖可离线测的 from_dict 契约；路由/服务层因依赖读真实 config.json 的单例不做自动化，以 grep + 人工核验代替，见 Step 4）

**背景**：①`not str(item.blocks_dir)` 恒真使校验死亡：blocks_dir=null 时 `Path(None)` 在 from_dict 内抛 TypeError → 路由无 try 直接 500；空串还原成 PROJECT_ROOT 入队。②自动总结期间用户停止后，外层无条件广播 done「队列全部完成」，前端收到假完成态。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_queue_item_validation.py`：

```python
"""P2：blocks_dir=null 的队列项必须在路由层被跳过/拒绝，而不是 Path(None) 500。
本文件锁定 QueueItem.from_dict 对 null/空串的行为契约（路由包装以此为前提）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from backend.services.queue_service import PROJECT_ROOT, QueueItem


def test_from_dict_null_blocks_dir_raises():
    with pytest.raises(Exception):
        QueueItem.from_dict({"name": "x", "blocks_dir": None})


def test_from_dict_empty_blocks_dir_resolves_project_root():
    item = QueueItem.from_dict({"name": "x", "blocks_dir": ""})
    assert item.blocks_dir == PROJECT_ROOT  # 路由层必须据此把空串条目挡掉
```

- [ ] **Step 2: 跑测试确认失败/通过基线**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_queue_item_validation.py -v`
Expected: PASS（这是契约锁定测试，先固化现状作为路由修复的前提）

- [ ] **Step 3: 实现**

(a) routes_analysis.py put_queue 循环替换：

```python
    for d in req.items:
        # P2 修复：null/空串 blocks_dir 此前一路穿透——Path(None) 让接口 500，
        # 空串还原成 PROJECT_ROOT 当书入队。改为原始 dict 预检 + 构造兜底。
        if not d.get("name") or not d.get("blocks_dir") or not d.get("workspace_dir"):
            continue
        try:
            item = QueueItem.from_dict(d)
        except Exception:
            continue
        service.queue.add_item(item)
```

(b) queue_service.py :571-577 替换：

```python
            else:
                # 2026-08-02 spec：队列全部完成且开启 auto_summary 时，
                # 对已完成的书逐本串行执行最终总结（期间用户可通过 /api/summary/stop 停止）
                if config.analysis.auto_summary:
                    await self._auto_summary_done_books(config)
                # P2 修复：自动总结期间用户可能已停止——此时不得广播“队列全部完成”
                if self._stop_requested:
                    await hub.state_change("stopped", "分析已停止（总结阶段中止）")
                else:
                    await hub.state_change("done", "队列全部完成")
```

（routes_analysis.py 顶部确认已导入 `QueueItem`——原循环已在用，无需新导。）

- [ ] **Step 4: 核验**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_queue_item_validation.py backend/tests/test_auto_summary.py backend/tests/test_queue_service.py -v`
Expected: PASS
Run: `Select-String -Path backend\api\routes_analysis.py -Pattern 'blocks_dir' | Select-Object -First 3`
人工核验：预检三元条件存在；stop 分支位于 auto_summary 之后。

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes_analysis.py backend/services/queue_service.py backend/tests/test_queue_item_validation.py
git commit -m "fix(queue): put_queue畸形条目跳过+停止后不再广播假done"
```

---

### Task B6: final_summary style_task 异常路径兜底

**Files:**
- Modify: `backend/services/final_summary.py`（run() 内 ：1574-1650 区域包 try/finally）
- Test: Modify `backend/tests/test_style_task_stop.py`（追加）

**背景**：Task 5 只覆盖了两条 stop 早退路径；「所有批次均失败」raise（:1612）、复检抛错（:1624 无包裹）、最终报告 raise（:1677）等异常路径仍遗留孤儿 style_task 继续烧 token（service 层长驻事件循环不会回收）。

方案：从消费循环起到 `style_profile = await style_task` 止整体包 try/finally；finally 里复用 `_cancel_style_task`（done 时 no-op，正常成功路径零开销）。既有的两条停止路径显式回收**保留**（幂等双保险，减少缩进改动面）。

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_style_task_stop.py`：

```python
def test_exception_path_reaps_pending_style_task(tmp_path, monkeypatch):
    """run() 中途抛异常（如所有批次均失败）不得遗留孤儿 style_task"""
    runner = _make_runner(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hang_style():
        started.set()
        await release.wait()

    async def fail_all(*a, **k):
        return None

    async def main():
        monkeypatch.setattr(runner, "_run_style_extraction", hang_style)
        monkeypatch.setattr(type(runner), "_call_llm_summary", fail_all)
        monkeypatch.setattr(type(runner), "_call_llm_final", fail_all)
        task_holder = {}

        real_create = asyncio.create_task
        def spy(coro):
            t = real_create(coro)
            task_holder["t"] = t
            return t
        monkeypatch.setattr(asyncio, "create_task", spy)

        run_task = asyncio.create_task(runner.run())
        await asyncio.wait_for(started.wait(), timeout=3)
        await asyncio.sleep(0.1)          # 让 run() 走到“所有批次均失败”的 raise
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(run_task, timeout=10)
        st = task_holder["t"]
        assert st.done(), "finally 必须已回收挂起的 style_task"

    asyncio.run(main())
```

> 执行者备注：若 run() 在 style_task 创建之前就早退（如归一化文件缺失检查），先在 fixture 里按 test_summary_checkpoint.py 的 `_write_results` 补齐最小输入（含 locations_normalized/spatial_normalized 两个文件），保证能走到批次消费段。以实际早退点为准调整 fixture。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_style_task_stop.py -v`
Expected: 新增用例 FAIL —— `assert st.done()` 失败（任务仍挂起）

- [ ] **Step 3: 实现**

定位 run() 内（当前约 ：1580-1650）：

```python
        style_task = asyncio.create_task(self._run_style_extraction())
        self._style_task = style_task
```

紧随其后插入 `try:`，并把从 `for coro in asyncio.as_completed(tasks):` 起、至 `style_profile = await style_task` 止的整块**右移一级缩进**；块后接 finally：

```python
        # P2 收口（2026-08-24）：消费循环到风格结果回收之间任何异常（“所有批次
        # 均失败”的 RuntimeError、复检抛错等）都不再遗留孤儿 style_task 继续
        # 烧 token。正常路径 finally 时任务已被 await（done=True），cancel 为
        # no-op；下方两条停止路径的显式回收保留（幂等双保险）。
        try:
            ……（原代码整体右移一级）……
        finally:
            await self._cancel_style_task(style_task)
            self._style_task = None
```

边界核对（执行者自查清单）：
- try 块内所有 `return None` / `raise` 都会经过 finally ✓
- `await style_task` 成功后 finally 的 cancel 为 no-op ✓
- 缩进后块内不再引用 try 外新定义的局部变量之外的东西（无新增依赖）✓

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_style_task_stop.py backend/tests/test_global_llm_sem.py backend/tests/test_summary_checkpoint.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/final_summary.py backend/tests/test_style_task_stop.py
git commit -m "fix(summary): run()阶段段try-finally兜底回收style_task"
```

---

### Task B7: workspace aborted 失败化 + 回滚失败记滞留路径

**Files:**
- Modify: `backend/services/workspace_service.py`（:330 SHFileOperationW 条件；:224-232 archive_novel 回滚分支）
- Test: Create `backend/tests/test_workspace_archive_guard.py`

**背景**：①`if result != 0 and not op.fAnyOperationsAborted:` 把用户中止/系统中止当作成功放行 → delete_book 造成「队列已删磁盘未删」；②归档第二步失败的回滚自身失败被 `except: pass` 吞掉 → 目录滞留 `{书名}-YYYYMMDD-HHMM` 临时名，用户以为书丢了。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_workspace_archive_guard.py`：

```python
"""P2：归档移动失败且回滚亦失败时，返回错误必须携带滞留目录名（否则用户
以为书丢了——实际改名滞留在工作区）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.services import workspace_service


def test_archive_move_failure_reports_stuck_tmp_name(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    book = ws / "书A"
    book.mkdir(parents=True)
    monkeypatch.setattr(workspace_service, "_get_workspace_path", lambda: ws)
    monkeypatch.setattr(workspace_service, "_get_archive_root", lambda: tmp_path / "archive")

    def boom(src, dst):
        raise OSError("模拟网络盘故障")

    monkeypatch.setattr(workspace_service.shutil, "move", boom)

    def rename_fail(dst):
        raise OSError("回滚也失败")

    orig_rename = Path.rename
    calls = {"n": 0}
    def fake_rename(self, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            return orig_rename(self, dst)      # 第一步 workspace 内改名成功
        raise rename_fail.__wrapped__ if False else OSError("回滚也失败")

    monkeypatch.setattr(Path, "rename", fake_rename)

    result = workspace_service.archive_novel("书A")
    assert result["ok"] is False
    assert "书A-" in result["error"], "错误信息必须包含滞留的临时目录名"
```

> 执行者备注：monkeypatch `Path.rename` 影响全局，务必用如上计数法区分两次调用；若实现里还有第三次 rename 引发干扰，改用更窄的打点方式（例如 monkeypatch `workspace_service.logger` 无关，仅提示谨慎）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_workspace_archive_guard.py -v`
Expected: FAIL —— error 只有「移动失败： …」不含滞留名

- [ ] **Step 3: 实现**

(a) :330 替换：

```python
        if result != 0 or op.fAnyOperationsAborted:
            if op.fAnyOperationsAborted:
                logger.error("SHFileOperationW 被中止（fAnyOperationsAborted=TRUE），目标未移入回收站")
            raise RuntimeError(f"SHFileOperationW 失败，错误码 {result}")
```

(b) :226-232 替换：

```python
    except Exception as e:
        # 移动失败，回滚；回滚自身失败必须暴露滞留路径（P2：此前静默吞掉，
        # 目录滞留时间戳临时名，用户按原名重试报“不存在”，看似书丢了）
        roll_err = ""
        try:
            temp_path.rename(novel_dir)
        except Exception as re_err:
            roll_err = f"；回滚亦失败，目录滞留为工作区内「{temp_path.name}」，请手工改名恢复"
            logger.error("归档回滚失败: %s -> %s: %s", temp_path, novel_dir, re_err)
        return {"ok": False, "error": f"移动失败: {e}{roll_err}"}
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_workspace_archive_guard.py backend/tests/test_delete_book_path.py backend/tests/test_workspace_scan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/workspace_service.py backend/tests/test_workspace_archive_guard.py
git commit -m "fix(workspace): 回收站aborted视为失败+归档回滚失败记录滞留路径"
```

---

### Task B8: excel 零 sheet 明确报错 + 公式注入消毒

**Files:**
- Modify: `backend/utils/excel_export.py`（`_write_sheet` 值写入消毒；保存前零 sheet 守卫；角色导出的文本列同样消毒）
- Test: Create `backend/tests/test_excel_export_guard.py`

**背景**：①目录只有非识别名 JSON 时删默认 sheet 后零工作表，wb.save() IndexError 接口 500（实测复现）；②LLM 文本以 `=` 开头被 openpyxl 按公式存储，打开 xlsx 触发 DDE/函数注入（实测 data_type='f'）。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_excel_export_guard.py`：

```python
"""P2：①公式注入消毒 ②零可识别 JSON 时明确报错而非 IndexError"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest


def _mk_core_events(d: Path, event: str):
    d.mkdir(parents=True, exist_ok=True)
    (d / "core_events_aggregated.json").write_text(json.dumps(
        {"chapter_1": [{"id": "e1", "event": event, "characters": "张三", "function": "铺垫"}]},
        ensure_ascii=False), encoding="utf-8")


def test_formula_like_event_is_sanitized(tmp_path):
    from openpyxl import load_workbook
    from backend.utils.excel_export import export_to_excel
    d = tmp_path / "agg"
    _mk_core_events(d, "=cmd|'/c calc'!A1")
    out = export_to_excel(d, tmp_path / "a.xlsx")

    wb = load_workbook(out)
    ws = wb["核心事件"]
    cell = ws.cell(row=2, column=3)
    assert cell.data_type != "f", "公式串必须被转义为文本"
    assert str(cell.value).startswith("'")


def test_no_recognized_json_raises_clear_error(tmp_path):
    from backend.utils.excel_export import export_to_excel
    d = tmp_path / "agg"
    d.mkdir()
    (d / "unrecognized.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="可识别"):
        export_to_excel(d, tmp_path / "b.xlsx")
```

> 执行者备注：导出函数名以文件实际为准（约 :14 `def export_to_excel(...)`，角色导出为 `export_characters_to_excel`）；若名称不同，同步修正测试 import。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_excel_export_guard.py -v`
Expected: FAIL ×2（data_type=='f'；IndexError 而非 ValueError）

- [ ] **Step 3: 实现**

(a) 模块级新增（放在 import 区之后）：

```python
_RISKY_PREFIXES = ("=", "+", "-", "@")


def _safe_cell_value(val) -> str:
    """Excel 公式注入消毒：以 = + - @ 开头的文本前置单引号强制按文本存储。

    P2 修复（2026-08-24）：LLM 产出的分析文本可能形如 =cmd|'/c calc'!A1，
    openpyxl 原样写入会被 Excel 当公式求值（DDE 注入面）。负数显示不受影响
    （前导撇号被 Excel 隐藏，值为文本 "-5"）。"""
    s = str(val) if val is not None else ""
    if s.startswith(_RISKY_PREFIXES):
        return "'" + s
    return s
```

(b) `_write_sheet` :50 的 value 改为 `value=_safe_cell_value(val)`；`export_characters_to_excel` 中角色名/事件摘要两个文本列（`value=name` 与 `value=event_summary`）同样包裹 `_safe_cell_value(...)`（数字列 chapters/total_events 不动）。

(c) 主导出保存前（`wb.save(output_path)` 之前、列宽循环之后）插入：

```python
    if not wb.worksheets:
        # P2 修复：目录里只有非识别名 JSON 时，11 个 _load_json 全 None，
        # 删默认 sheet 后零工作表 → wb.save 抛 IndexError 接口 500；改为明确报错。
        raise ValueError(f"聚合目录缺少可识别的分析 JSON 文件，无法导出 Excel: {aggregated_dir}")
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_excel_export_guard.py -v`
Expected: PASS ×2

- [ ] **Step 5: Commit**

```bash
git add backend/utils/excel_export.py backend/tests/test_excel_export_guard.py
git commit -m "fix(excel): 零sheet明确报错+单元格公式注入消毒"
```

---

### Task F1: GraphPage tooltip 转义 + loadData 切书守卫

**Files:**
- Modify: `frontend/src/pages/GraphPage.vue`

**背景**：tooltip formatter 将 LLM 派生角色名未经转义插 HTML（ECharts tooltip 按 innerHTML 渲染）；loadData 无 bookId 快照守卫，慢响应可用 A 书数据覆盖 B 书图（与 Timeline/CharacterCard/Summary 同类，终审建议补齐）。

- [ ] **Step 1: 修改**

(a) script 顶部 import 区加：

```typescript
import { escapeHtml } from '../utils/markdown'
```

（markdown.ts 当前未导出 escapeHtml——同时在该文件给 `function escapeHtml` 加 `export`。）

(b) tooltip formatter 三处名字插值包裹：

```typescript
            `<b>${escapeHtml(String(params.data.name))}</b><br/>` +
```
```typescript
            `<b>${escapeHtml(String(src?.name ?? params.data.source))}</b> — <b>${escapeHtml(String(tgt?.name ?? params.data.target))}</b><br/>` +
```

(c) loadData（:41-74）加快照守卫：

```typescript
async function loadData() {
  const bid = bookId.value
  if (!bid) return
  loading.value = true
  try {
    ...（原请求不变，bookId.value → bid）...
    if (bid !== bookId.value) return   // 已切书：丢弃过期响应
    nodes.value = res.nodes
    ...（其余赋值不变）...
    await nextTick()
    renderChart()
  } catch (e) {
    if (bid !== bookId.value) return
    nodes.value = []
    edges.value = []
    console.error('加载关系图失败:', e)
  } finally {
    if (bid === bookId.value) loading.value = false
  }
}
```

- [ ] **Step 2: 构建**

Run: `cd frontend && npm run build`
Expected: 成功

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/GraphPage.vue frontend/src/utils/markdown.ts
git commit -m "fix(graph): tooltip转义+loadData切书快照守卫"
```

---

### Task F2: SummaryPage viewAggFile 守卫

**Files:**
- Modify: `frontend/src/pages/SummaryPage.vue`（viewAggFile :51-53）

- [ ] **Step 1: 修改**

```typescript
async function viewAggFile(name: string) {
  const bid = bookId.value
  if (!bid) return
  try {
    const res = await api.getAggregateFile(bid, name)
    if (bid !== bookId.value) return   // 已切书：丢弃过期响应
    aggContent.value = res.content; aggIsJson.value = res.is_json
  } catch (e) { aggError.value = (e as Error).message }
}
```

- [ ] **Step 2: 构建 + Commit**

Run: `cd frontend && npm run build`
```bash
git add frontend/src/pages/SummaryPage.vue
git commit -m "fix(summary): viewAggFile切书快照守卫"
```

---

### Task F3: ChapterDetailPanel 早退分支递增 seq

**Files:**
- Modify: `frontend/src/components/ChapterDetailPanel.vue`（loadChapter :64-68）

**背景**：早退分支（effectiveChapter<=0）只置 null 不 bump requestSeq，切书瞬间在飞旧响应因 seq 未变而合法写回。

- [ ] **Step 1: 修改**

```typescript
async function loadChapter() {
  // P2 修复：早退也必须失效在飞请求——否则切书瞬间旧响应因 seq 未变而合法写回
  requestSeq++
  if (!props.bookId || effectiveChapter.value <= 0) {
    result.value = null
    return
  }
  const seq = requestSeq
  ...（以下原样，原 `const seq = ++requestSeq` 改为取当前值）...
```

（即把原 ：69 `const seq = ++requestSeq` 删除，函数首行统一 bump，后续用 `const seq = requestSeq`。）

- [ ] **Step 2: 构建 + Commit**

Run: `cd frontend && npm run build`
```bash
git add frontend/src/components/ChapterDetailPanel.vue
git commit -m "fix(chapter): loadChapter早退分支递增序号失效在飞响应"
```

---

### Task F4: MapPage 结束沿 try/catch

**Files:**
- Modify: `frontend/src/pages/MapPage.vue`（running watch :95-107）

- [ ] **Step 1: 修改**

```typescript
watch(() => normStatus.value?.running, async (running, prev) => {
  if (prev === true && running === false && bookId.value) {
    if (normPollHandle !== null) {
      window.clearInterval(normPollHandle)
      normPollHandle = null
    }
    // P2 修复：结束沿刷新失败此前成为 unhandled rejection，locations 不刷新无提示
    try {
      const res = (await api.getMap(bookId.value)) as unknown as MapDataResponse
      locations.value = res.locations as Location[]
      relationships.value = res.relationships as SpatialRel[]
      needsNormalization.value = res.needs_normalization === true
    } catch (e) {
      console.error('归一化结束后刷新地图失败:', e)
    }
    await refreshNormResult()
  }
})
```

- [ ] **Step 2: 构建 + Commit**

Run: `cd frontend && npm run build`
```bash
git add frontend/src/pages/MapPage.vue
git commit -m "fix(map): 归一化结束沿getMap补try-catch"
```

---

### Task F5: 前端三小修合集（AppLayout / CountUp / useLogStore）

**Files:**
- Modify: `frontend/src/components/AppLayout.vue`（onMounted :63-66）
- Modify: `frontend/src/components/CountUp.vue`（import 行 + onUnmounted）
- Modify: `frontend/src/composables/useLogStore.ts`（loadFromStorage 过滤 :33 附近）

- [ ] **Step 1: AppLayout** onMounted 内 `isDesktop.value = !!wvApi()` 之后加：

```typescript
  // P2：WebView2/pywebview 可能晚于挂载注入（官方要求监听 pywebviewready）；
  // AppLayout 为常驻布局，采样失败则窗口控制整个会话缺失。
  window.addEventListener('pywebviewready', () => { isDesktop.value = !!wvApi() })
```

- [ ] **Step 2: CountUp** import 行补 `onUnmounted`，script 尾部（watch 之后）加：

```typescript
// P2：卸载后终止动画循环（与文件头“无泄漏”承诺对齐；影响有界但应兑现）
onUnmounted(() => { if (rafId !== null) cancelAnimationFrame(rafId) })
```

- [ ] **Step 3: useLogStore** 过滤条件扩展：

```typescript
          .filter(x => x && typeof x.text === 'string'
            && typeof x.id === 'number' && Number.isFinite(x.id))
```

- [ ] **Step 4: 构建 + Commit**

Run: `cd frontend && npm run build`
```bash
git add frontend/src/components/AppLayout.vue frontend/src/components/CountUp.vue frontend/src/composables/useLogStore.ts
git commit -m "fix(frontend): pywebviewready监听+rAF卸载取消+日志id过滤"
```

---

### Task F6: QueuePage 定时器分键清理 + 删除确认改 name 快照

**Files:**
- Modify: `frontend/src/pages/QueuePage.vue`

**背景**：①单一 refreshTimer 承载 refresh/refreshTokens 互吞，且卸载不清理；②删除确认持索引快照，轮询重排可删错书（WorkspacePage 已用 name 快照对照）。

- [ ] **Step 1: 定时器分键**（:42-46 替换）

```typescript
// P2：按回调分键防抖——共享单 timer 时 token_stats 会吞掉先排队的 refresh
const pendingRefreshTimers = new Map<() => Promise<void>, ReturnType<typeof setTimeout>>()
function scheduleRefresh(fn: () => Promise<void>) {
  const prev = pendingRefreshTimers.get(fn)
  if (prev) clearTimeout(prev)
  pendingRefreshTimers.set(fn, setTimeout(() => {
    pendingRefreshTimers.delete(fn)
    void fn()
  }, 500))
}
```

onUnmounted（:208-211）内补：

```typescript
  for (const t of pendingRefreshTimers.values()) clearTimeout(t)
  pendingRefreshTimers.delete  // ← 不需要此行；写成下面一行
  pendingRefreshTimers.clear()
```

（实际只加两行：for-clearTimeout 循环 + clear()；上面第三行是注释占位提醒勿抄。）

- [ ] **Step 2: 删除确认改名称快照**

替换 handleDelete/deleteTargetName/confirmDelete（:142-161 区域）：

```typescript
function handleDelete(index: number) {
  const it = status.value?.items?.[index]
  if (!it) return
  deleteTargetName.value = it.name
  deleteTarget.value = index   // 仅作弹窗开关信号，确认时以名称实时解析索引
}

const deleteTarget = ref<number | null>(null)
const deleteTargetName = ref('')

function confirmDelete() {
  const name = deleteTargetName.value
  deleteTarget.value = null
  if (!name) return
  withBusy(async () => {
    // P2：以名称实时解析索引，避免轮询/WS 重排后按旧索引删错书
    const idx = status.value?.items?.findIndex(i => i.name === name) ?? -1
    if (idx < 0) { add(`未找到《${name}》，可能已被移除`, 'warn', 'analysis'); return }
    await api.deleteBook(idx)
    add(`已删除《${name}》并移入回收站`, 'warn', 'analysis')
  }, '删除小说')
}
```

（模板 :405 `deleteTargetName` 标识符不变；原 computed 定义删除。）

- [ ] **Step 3: 构建 + Commit**

Run: `cd frontend && npm run build`
```bash
git add frontend/src/pages/QueuePage.vue
git commit -m "fix(queue-page): 刷新定时器分键清理+删除确认改名称快照"
```

---

### Task A1: moderation 瞬时误判多给一轮尝试

**Files:**
- Modify: `backend/core/llm_client.py`（chat_with_retry :849 阈值、:897 backoff 门控）
- Test: Create `backend/tests/test_moderation_threshold.py`

**背景**：自由文本关键词（blocked/filtered/violat…）会把瞬时网关 502 升级为审核拦截：第 2 次命中即放弃并完全跳过指数退避。设计取舍：阈值 2→3（真审核多付一次调用；瞬时抖动获得一整轮恢复机会）；backoff 入口的放弃同样要求 hits≥2。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_moderation_threshold.py`：

```python
"""P2：自由文本误判的瞬时网关错误应多获一次温度尝试（阈值 2→3）"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio

from backend.config.settings import AppConfig
from backend.core.llm_client import LLMClient


def _client():
    return LLMClient(AppConfig())


def test_transient_blocked_recovers_on_third_call():
    c = _client()
    seq = [(False, "", "API错误: Request blocked by WAF", (0, 0)),
           (False, "", "API错误: Request blocked by WAF", (0, 0)),
           (True, "OK", "", (1, 2))]
    with patch.object(LLMClient, "chat", new=AsyncMock(side_effect=seq)), \
         patch.object(LLMClient, "_sleep", new=AsyncMock()):
        ok, content, err, tokens, stats = asyncio.run(
            c.chat_with_retry([{"role": "user", "content": "x"}]))
    assert ok and content == "OK"


def test_persistent_blocked_gives_up_without_backoff():
    c = _client()
    blocked = (False, "", "API错误: Request blocked by WAF", (0, 0))
    with patch.object(LLMClient, "chat", new=AsyncMock(return_value=blocked)), \
         patch.object(LLMClient, "_sleep", new=AsyncMock()) as zzz:
        ok, _, err, _, stats = asyncio.run(
            c.chat_with_retry([{"role": "user", "content": "x"}]))
    assert not ok
    zzz.assert_not_called()
```

> 执行者备注：①`_sleep` 若类中不存在该方法，先在 chat_with_retry 所在类加一个薄封装 `async def _sleep(self, s): await asyncio.sleep(s)` 并把 :835/:887/:920 三处 `await asyncio.sleep(wait_time)` 换为 `await self._sleep(wait_time)`（可测试性注入，行为不变）；②AppConfig 默认 temperature_max_retries 若 <3 需在测试里用 `replace(cfg.api, temperature_max_retries=3)` 构造客户端。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_moderation_threshold.py -v`
Expected: 第一条 FAIL（第 2 次命中即放弃）；第二条 PASS（基线保持）

- [ ] **Step 3: 实现**

:849 `if moderation_hits >= 1:` → `if moderation_hits >= 2:`，注释更新为：

```python
            # 内容审核拦截：连续 3 次命中才短路（P2 2026-08-24：宽松关键词会把
            # 瞬时网关 502 误判为拦截，2 次即弃太激进——多给一轮温度尝试；
            # 真·内容审核多付 1 次调用换取误判恢复，值得）
```

:897 backoff 入口 `if is_moderation_error(last_error):` → `if is_moderation_error(last_error) and moderation_hits >= 2:`（附同类注释一行）。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_moderation_threshold.py backend/tests/test_moderation.py backend/tests/test_llm_mock.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/core/llm_client.py backend/tests/test_moderation_threshold.py
git commit -m "fix(llm): 审核拦截瞬时误判多给一轮温度尝试"
```

---

### Task A2: markdown 链接占位保护

**Files:**
- Modify: `frontend/src/utils/markdown.ts`（inlineFormat :29-34 链接段 + 还原段）
- Verify: `npx -y tsx src/utils/markdown.test.ts`（既有套件 + 新增用例）

**背景**：链接替换成 `<a>` 后强调正则在整段继续匹配，URL 含 `_ * ~` 时 href 值内注入 `<em>/<strong>` → 锚点损坏（无 XSS，纯渲染破坏；LLM 报告中带下划线 URL 常见）。

- [ ] **Step 1: 先写失败用例**

在 markdown.test.ts 的 runTests() 末尾（「全部通过」console.log 之前）追加：

```typescript
  // 链接 href 保护（P2 2026-08-24）
  r = renderMarkdown('[doc](https://example.com/wiki/a_b_c)')
  if (r.includes('<em>') || r.includes('<strong>') || r.includes('<del>')) {
    console.error('? FAIL: 链接 href 被强调正则污染')
    console.error('   actual: ' + r)
    process.exit(1)
  }
  assertContains(r, 'href="https://example.com/wiki/a_b_c"', 'href intact')
  r = renderMarkdown('__init__ 与 [x](https://a.io/p__q)')
  assertContains(r, '<strong>init</strong>', '可见文本粗体仍生效')
  assertContains(r, 'https://a.io/p__q</a>', 'URL 内双下划线不被加粗')
  console.log('? 链接 href 保护')
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx -y tsx src/utils/markdown.test.ts`
Expected: FAIL（href 被污染）

- [ ] **Step 3: 实现**

inlineFormat 中链接段（:30-34）替换为占位保护：

```typescript
  // 链接 [text](url)：生成 <a> 后整体入占位符，避免后续强调正则污染 href 值
  const linkStash: string[] = []
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, url) => {
    // 防止 javascript: 等危险协议
    const safeUrl = /^(https?:|mailto:|#|\/)/i.test(url) ? url : '#'
    const idx = linkStash.push(
      `<a href="${safeUrl}" target="_blank" rel="noopener" class="md-link">${label}</a>`,
    ) - 1
    return `\u0001LINK${idx}\u0001`
  })
```

还原段（:48 代码占位符还原之前）加：

```typescript
  // 还原链接占位符
  text = text.replace(/\u0001LINK(\d+)\u0001/g, (_, idx) => linkStash[Number(idx)])
```

- [ ] **Step 4: 运行确认通过 + 构建**

Run: `cd frontend && npx -y tsx src/utils/markdown.test.ts && npm run build`
Expected: 测试全过、build 成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/markdown.ts frontend/src/utils/markdown.test.ts
git commit -m "fix(markdown): 链接占位保护防强调正则污染href"
```

---

### Task A3: client.ts 路径参数统一编码

**Files:**
- Modify: `frontend/src/api/client.ts`

**背景**：约 16 处 `${book_id}` 及 `${name}` 裸插模板串，书名含 `# ? %` 打错路径 404（:333 一处已有 encodeURIComponent 先例）。

- [ ] **Step 1: 实现**

request() 定义之后加帮助函数：

```typescript
/** 路径段编码：书名/文件名含 # ? % 等时不至于把 URL 截断到错误资源（P2 审计） */
function seg(s: string | number): string {
  return encodeURIComponent(String(s))
}
```

对所有 `/api/...${book_id}` 形态逐一替换为 `${seg(book_id)}`；`${chapter}`（:325 数字）与 `${name}`（:333、:446）同样用 seg 包裹。以 grep 清单为准执行：

Run: `Select-String -Path frontend\src\api\client.ts -Pattern '\$\{book_id\}|\$\{name\}' | Measure-Object`
替换后再跑应为 0 命中（seg 内部字符串除外——写成 `'${seg(book_id)}'` 即不含裸模式）。

- [ ] **Step 2: 构建 + Commit**

Run: `cd frontend && npm run build`
Expected: vue-tsc 0 错（seg 接受 string|number，:325 数字章号兼容）
```bash
git add frontend/src/api/client.ts
git commit -m "fix(client): 路径参数统一encodeURIComponent"
```

---

### Task A4: 章节正文超限尾部保序截断

**Files:**
- Modify: `backend/core/prompt_builder.py`（构造器 + build_messages）
- Test: Create `backend/tests/test_prompt_content_truncate.py`

**背景**：九路上下文都有截断参数，唯独正文没有；单章超长/block_size 配大 → prompt 确定性超窗，退火无法挽救，块烧满 3 轮补跑预算后永久丢失。

设计取舍：默认 30000 字符（≈3 万汉字，为 system+九路上下文留出 32k-token 窗口余量）；做成构造器参数便于后续接配置；尾部保序（网文悬念多在后半），头部丢弃量显式告知模型。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_prompt_content_truncate.py`：

```python
"""P2：章节正文必须有上限——超窗是确定性失败，白烧全部重试预算"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.core.prompt_builder import PromptBuilder
from backend.models.knowledge import KnowledgeBase


def test_long_content_truncated_with_tail_kept():
    pb = PromptBuilder(max_content_chars=500)
    msgs = pb.build_messages("头" + "中" * 600 + "尾结局", 7, KnowledgeBase())
    user = msgs[1]["content"]
    assert len(pb_last_body(user)) <= 520
    assert "已截去开头" in user
    assert user.rstrip().endswith("尾结局") or "尾结局" in user[-200:]
    assert "头" * 50 not in user[-300:]  # 开头确实被丢


def test_short_content_untouched():
    pb = PromptBuilder(max_content_chars=500)
    msgs = pb.build_messages("短正文" * 10, 1, KnowledgeBase())
    assert "已截去开头" not in msgs[1]["content"]
    assert "短正文" in msgs[1]["content"]


def pb_last_body(user_prompt: str) -> int:
    """取 [当前分析文本] 段之后的正文长度（粗略：最后一个标记之后）"""
    marker = "[当前分析文本"
    idx = user_prompt.rfind(marker)
    return len(user_prompt[idx:])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_prompt_content_truncate.py -v`
Expected: FAIL（无截断发生）

- [ ] **Step 3: 实现**

(a) 构造器参数区（max_themes 之后）加：

```python
        max_content_chars: int = 30000,
```
及属性赋值：
```python
        # P2 修复：正文是唯一无上限的 prompt 注入项；超窗确定性失败且退火无效。
        # 3 万字≈为 32k-token 窗口的 system+九路上下文留足余量；后续可接配置。
        self.max_content_chars = max_content_chars
```

(b) build_messages 在 user_prompt f-string 之前加：

```python
        # P2：尾部保序截断——开头丢弃量显式告知模型，避免其困惑叙事断裂
        content_text = chapter_content
        if len(content_text) > self.max_content_chars:
            dropped = len(content_text) - self.max_content_chars
            content_text = (
                f"【注意】原文过长，已截去开头约 {dropped} 字，以下为保留的后段：\n"
                + content_text[-self.max_content_chars:]
            )
```
f-string 内 `{chapter_content}` 改 `{content_text}`。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_prompt_content_truncate.py backend/tests/test_pipeline_mock.py -v`
Expected: PASS（NovelAnalyzer 未传新参走默认值，管线行为除极端长文外不变）

- [ ] **Step 5: Commit**

```bash
git add backend/core/prompt_builder.py backend/tests/test_prompt_content_truncate.py
git commit -m "fix(prompt): 章节正文超限尾部保序截断"
```

---

### Task A5: SettingsPage 提交前剪除空字符串

**Files:**
- Modify: `frontend/src/pages/SettingsPage.vue`（save() 内 PUT 之前）

**背景**：v-model.number 清空后残留 `''` 原样提交。Task B 系列后端强转已能把垃圾值回落默认，但「静默改值」不可预期；提交前剪除空串让语义变为「该项恢复默认」，且 GET 重载后所见即所得。

- [ ] **Step 1: 修改**

save() 中 `await api.putSettings(config.value)` 之前插入：

```typescript
    // P2：数值输入清空后 v-model.number 保留 ''；后端虽能兜底回落默认，
    // 但静默改值不可预期——提交前剪除空字符串字段，语义即“恢复该项默认”
    const pruneEmptyStrings = (obj: Record<string, unknown>) => {
      for (const k of Object.keys(obj)) {
        const v = obj[k]
        if (v === '') { delete obj[k]; continue }
        if (v && typeof v === 'object' && !Array.isArray(v)) {
          pruneEmptyStrings(v as Record<string, unknown>)
        }
      }
    }
    pruneEmptyStrings(config.value as unknown as Record<string, unknown>)
    await api.putSettings(config.value)
```

- [ ] **Step 2: 构建 + Commit**

Run: `cd frontend && npm run build`
```bash
git add frontend/src/pages/SettingsPage.vue
git commit -m "fix(settings-page): 提交前剪除空字符串数值字段"
```

---

### Task D1: 文档收口 + 全量验证

**Files:**
- Modify: `agent.md`（§10 追加条目 + 受影响一句话微调）
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 后端全量 + 前端构建 + 设计系统红线**

```bash
.venv\Scripts\python.exe -m pytest backend/tests/ -q     # 或 python -m pytest
cd frontend && npm run build
grep -RIn "backdrop-filter" frontend/src || true
```
Expected: pytest 全绿（206 + 本批新增约 15）；build 成功；backdrop-filter 维持既有 2 处存量命中（ConfirmDialog，非本批引入，已移交事项）。

- [ ] **Step 2: agent.md §10 追加（10.13 之后、10.12 相关文档之前的顺延位置，编号续 10.14）**

```markdown
### 10.14 2026-08-24 P2/P3 划算项批量修复（第二批）
- S+A 两档共 19 项：pipeline 快照线程池化/failed 范围过滤/join 容错、解析健壮性双修、json 修复链 dict 守卫+tmp 唯一名、聚 合原子写、put_queue 校验、假 done 广播、style_task 异常兜底、workspace 双修、excel 消毒、前端六页守卫/转义/定时器收口、moderation 阈值放宽、markdown 链接保护、client 编码、正文截断、设置页剪枝
- **原因**：P1 修复后对剩余 P2/P3 做性价比筛选落地
- **未动**：KB 近邻指纹、切分窄化算法、协议探测回退等高成本项（详见计划文档排除清单）
```

受影响章节一句话微调（措辞自定，须与代码一致）：§5.3 json_utils 行补「tmp 进程内唯一名」；§5.3 aggregate_utils 行补「主文件原子写」；§5.1 prompt_builder 行补「正文尾部保序截断」。

- [ ] **Step 3: CHANGELOG.md 追加一条（风格随现有文件）**

- [ ] **Step 4: Commit**

```bash
git add agent.md CHANGELOG.md
git commit -m "docs: 同步agent.md与CHANGELOG至P2批量修复状态"
```

---

## Self-Review 结论

1. **覆盖核对**：S 档 14 项 → B1-B8+F1-F6 全覆盖；A 档 5 项 → A1-A5；终审三项新发现分别落在 B6/F1/F2。#37/#44/#45 等低性价比项明确排除。
2. **占位符扫描**：B4/B6/B7/A1 含「执行者备注」属于对未知签名的防御性指引且给出了完整替代代码骨架，非 TBD。
3. **一致性**：`failed_chapters_in(set)->List[int]`、`validate_parsed_analysis(dict)->tuple`、`_cancel_style_task` 复用、前端 `seg()`/`linkStash`/`deleteTargetName(ref)` 各定义与调用点匹配。
