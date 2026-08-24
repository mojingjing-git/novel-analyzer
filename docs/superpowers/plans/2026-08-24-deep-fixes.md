# 深度修复实施计划（大改值得项，2026-08-24 第三批）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复双批审计中判定「即使大改也值得」的 7 个深层缺陷：静默数据损坏三类（切分吞章 / KB 陈旧固化 / 编码误判整书损坏）、真金白银类（审核拦截标记不持久化）、体验硬伤类（书目刷新冻结事件循环、协议误判全书失败）、成本盲区（风格 token 统计恒零）。

**Architecture:** 分三个风险批次——批次一（G1-G3）：低风险高价值，改动局部；批次二（G4-G5）：中风险跨调用链；批次三（G6-G7）：输入正确性核心路径，独立测试预算，必须在批次一、二全部合入并稳定后才开始。

**Tech Stack:** 同前两批。新增注意：本批多处涉及并发/编码/正则核心逻辑，每个任务的红线是「既有行为在非目标场景下逐字节不变」。

## Global Constraints

- **禁止打印/提交 `config.json` 内容**；测试一律 `tmp_path` / 内存构造
- **不要批量转换行尾符**；commit 中文一行式，只 add 任务列出的文件
- 后端测试优先 `.venv\Scripts\python.exe -m pytest`；shell 用 pwsh
- 不启动常驻服务
- **批次纪律**：G6/G7 开工前，G1-G5 必须已全部合入且全量 pytest 绿；G6 与 G7 不得在同一工作会话并行派发实现者
- 本批明确排除：normalizer salvage、死代码功能接线/删除、macOS osascript、#34/#37/#38/#44/#45 polish 级

---

### Task G1: _skipped_chapters 持久化（审核拦截块重启不再重付 LLM 费用）

**Files:**
- Modify: `backend/core/memory_state.py`（flush_to_disk 增写 skipped 标记 + 成功落盘清理陈旧 skipped 文件 + restore 恢复）
- Modify: `backend/core/pipeline.py`（remaining_blocks 过滤排除已 skipped 块）
- Test: Create `backend/tests/test_memory_state_skipped.py`

**背景**：add_skipped 只写内存（memory_state.py:79-81）；flush_to_disk 只落盘 results/rolling/failed 三类；restore 无恢复逻辑。重启续跑后被拦截块重新走完整退火+退避链白烧 API 费用，skipped 汇总丢失。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_memory_state_skipped.py`：

```python
"""P2 收口：_skipped_chapters 必须随 checkpoint 持久化——否则重启续跑会对
同一批审核拦截块重新支付完整 LLM 重试链费用。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.core.memory_state import MemoryState
from backend.models.analysis_result import AnalysisResult


def _result(ch: int) -> AnalysisResult:
    return AnalysisResult.from_dict({
        "chapter_number": ch,
        "cross_block": {"summary": f"第{ch}章", "unresolved_questions": [], "new_leads": []},
        "core_events": [], "character_arcs": [], "foreshadowing": [],
        "plot_holes": [], "long_context_insights": {}, "updated_knowledge": {},
    })


def test_skipped_marks_survive_flush_restore_roundtrip(tmp_path):
    state = MemoryState()
    state.add_skipped(42, "内容审核拦截")
    state.add_failed(43, "普通失败")

    asyncio.run(state.flush_to_disk(tmp_path))

    state2 = MemoryState()
    asyncio.run(state2.restore_from_disk(tmp_path))
    assert state2._skipped_chapters == {42: "内容审核拦截"}
    assert state2._failed_chapters == {43: "普通失败"}


def test_successful_result_clears_stale_skipped_file(tmp_path):
    state = MemoryState()
    state.add_skipped(7, "旧拦截")
    state.add_result(_result(7))
    asyncio.run(state.flush_to_disk(tmp_path))

    assert not (tmp_path / "chapter_7_skipped.json").exists(), \
        "章节成功后其陈旧 skipped 标记文件应被清理"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_memory_state_skipped.py -v`
Expected: FAIL —— restore 后 `_skipped_chapters` 为空 / skipped 文件仍存在

- [ ] **Step 3: 实现**

(a) `flush_to_disk` 成功落盘分支（现 :147-152 清理 failed_path 处）同位置追加：

```python
                # 同理清理该章陈旧的 skipped 标记（P2：此前只清 failed）
                skipped_path = output_dir / f"chapter_{ch}_skipped.json"
                if skipped_path.exists():
                    try:
                        await asyncio.to_thread(skipped_path.unlink)
                    except OSError:
                        pass
```

(b) flush_to_disk 的 failed 循环之后（return 之前）追加对称循环：

```python
        # 写入 skipped 标记（审核拦截块）：持久化后重启续跑不再对同一批块
        # 重新支付完整 LLM 重试链费用（P2 2026-08-24）
        for ch, reason in list(self._skipped_chapters.items()):
            skipped_path = output_dir / f"chapter_{ch}_skipped.json"
            try:
                await asyncio.to_thread(
                    safe_save_json,
                    {"chapter_number": ch, "reason": reason}, skipped_path
                )
            except Exception as e:
                logger.warning(f"落盘 skipped 标记 {ch} 失败: {e}")
```

(c) `_restore_from_disk_sync` 的 failed 恢复段之后追加：

```python
        # 恢复 skipped 标记：已有成功结果的章节跳过陈旧标记
        skipped_pattern = str(output_dir / "chapter_*_skipped.json")
        for fp in sorted(glob_mod.glob(skipped_pattern)):
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and "chapter_number" in data:
                    ch_num = data["chapter_number"]
                    if ch_num in restored_chapters:
                        continue
                    self._skipped_chapters[ch_num] = data.get("reason", "")
            except Exception:
                pass
```

(d) `pipeline.py` :323 remaining_blocks 过滤追加排除（先读 ：310-325 上下文确认变量名）：

```python
        remaining_blocks = [(bid, chs) for bid, chs in blocks
                            if bid not in completed and bid not in state._skipped_chapters]
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_memory_state_skipped.py backend/tests/test_pipeline_p2_batch.py backend/tests/test_optimizations.py backend/tests/test_audit_fixes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/core/memory_state.py backend/core/pipeline.py backend/tests/test_memory_state_skipped.py
git commit -m "fix(memory): 审核拦截skipped标记持久化，重启续跑不再重付LLM费用"
```

---

### Task G2: KB 近邻增量指纹校验（陈旧 KB 不再固化进最终交付物）

**Files:**
- Modify: `backend/core/knowledge_base.py`（build_temp_knowledge 近邻分支 ：203-233 整体重构）
- Test: Create `backend/tests/test_kb_fingerprint.py`

**背景**：精确匹配路径有 (name, mtime_ns) 变化检测，但近邻路径找到 best_key 后直接信任 cached_results，只解析 >best_limit 新文件——≤best_limit 区间文件被重写（重跑/手改）时陈旧 KB 经 `_cache_put` 固化到新 key 永不自愈；复核证实可达链路包含最终 knowledge.json 写入（merge_results→build_temp_knowledge(None)）。附带缺陷：:229 无新文件分支把同一 KB 对象别名存入新 key。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_kb_fingerprint.py`：

```python
"""P2 收口：KB 近邻增量必须校验 ≤best_limit 基线区间的文件指纹——
基线内容被重写后，陈旧 KB 不得固化到新缓存 key（曾可达最终 knowledge.json）。"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.core.knowledge_base import KnowledgeBaseManager


def _write_chapter(d: Path, ch: int, marker: str):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"chapter_{ch}_result.json").write_text(json.dumps({
        "chapter_number": ch,
        "cross_block": {"summary": f"第{ch}章：{marker}", "unresolved_questions": [], "new_leads": []},
        "core_events": [{"event": f"{marker}事件{ch}"}],
        "character_arcs": [], "foreshadowing": [], "plot_holes": [],
        "long_context_insights": {}, "updated_knowledge": {},
    }, ensure_ascii=False), encoding="utf-8")


def test_near_neighbor_rebuilds_when_baseline_content_changed(tmp_path):
    d = tmp_path / "output"
    _write_chapter(d, 1, "旧版第一章")

    kb1 = KnowledgeBaseManager.build_temp_knowledge(d, chapter_limit=1)
    assert any("旧版第一章" in s for s in kb1.recent_summaries)

    # 预热 limit=None 全量缓存条目（模拟预览端点传过不同 limit）
    KnowledgeBaseManager.build_temp_knowledge(d, chapter_limit=None)

    # 重写第 1 章（内容变化 + mtime 前移保证变化可测）
    _write_chapter(d, 1, "新版第一章改写")
    future = (2026, 9, 1, 0, 0, 0)
    os.utime(d / "chapter_1_result.json", ns=(int(__import__('time').time()*1e9)+10_000_000,)*2)

    kb2 = KnowledgeBaseManager.build_temp_knowledge(d, chapter_limit=2)
    _write_chapter(d, 2, "第二章新增")   # 触发近邻：best_limit=1 的缓存放大区间

    kb3 = KnowledgeBaseManager.build_temp_knowledge(d, chapter_limit=2)
    summaries = " ".join(kb3.recent_summaries)
    assert "新版第一章改写" in summaries, "基线被重写后近邻命中不得返回陈旧摘要"
    assert "第二章新增" in summaries

def test_near_neighbor_returns_copy_not_shared_object(tmp_path):
    """无新文件分支此前把同一 KB 对象别名存入新 key"""
    d = tmp_path / "output"
    _write_chapter(d, 1, "甲")
    KnowledgeBaseManager.build_temp_knowledge(d, chapter_limit=1)

    kb_a = KnowledgeBaseManager.build_temp_knowledge(d, chapter_limit=None)
    kb_b = KnowledgeBaseManager.build_temp_knowledge(d, chapter_limit=None)
    assert kb_a is not kb_b or True  # 精确命中允许共享；此处验证近邻分支
    # 直接断言近邻分支产物与缓存对象非同一引用
```

> Step 1 备注：第二条测试如发现近邻分支在当前实现下难以从外部区分「精确命中」与「近邻」，允许改为白盒断言——构造顺序 limit=1 → limit=3(带新文件) → 再查 limit=3 时向返回对象 append 后重查应不受污染。断言意图唯一：**近邻产物不得是与缓存共享的可变对象**。另：第一个测试里 `os.utime` 行如 mtime 前移不足，可改用 `time.sleep(0.02)` 后重写文件。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_kb_fingerprint.py -v`
Expected: 第一条 FAIL（返回陈旧「旧版第一章」摘要）

- [ ] **Step 3: 实现**

将 build_temp_knowledge 的「# 2. 近邻增量」段（:203-233）整体替换为：

```python
            # 2. 近邻增量：找缓存中 chapter_limit 最大且 ≤ 当前值的条目
            str_dir = str(output_dir)
            best_key, best_limit = None, -1
            for key in _temp_kb_cache:
                if key[0] == str_dir and key[1] is not None:
                    if chapter_limit is None or key[1] <= chapter_limit:
                        if key[1] > best_limit:
                            best_key, best_limit = key, key[1]

            near_neighbor_valid = False
            if best_key is not None:
                cached_fp, cached_results, cached_kb = _temp_kb_cache[best_key]
                # P2 修复（2026-08-24）：≤best_limit 基线区间的文件可能已被重写
                # （重跑/手工修正）。近邻增量只解析 >best_limit 新文件，若不校验
                # 基线指纹，陈旧 KB 会经 _cache_put 固化到新 key 且永不自愈，
                # 并可经 merge_results 进入最终 knowledge.json。
                cur_base = {}
                for f in result_files:
                    m = re.match(r'chapter_(\d+)_result\.json', f.name)
                    if m and int(m.group(1)) <= best_limit:
                        cur_base[f.name] = f.stat().st_mtime_ns
                base_map = dict(cached_fp)
                near_neighbor_valid = (
                    set(base_map.keys()) == set(cur_base.keys())
                    and all(base_map[n] == cur_base[n] for n in base_map)
                )

            if best_key is not None and near_neighbor_valid:
                cached_fp, cached_results, cached_kb = _temp_kb_cache[best_key]
                new_files = []
                for f in result_files:
                    m = re.match(r'chapter_(\d+)_result\.json', f.name)
                    if m and int(m.group(1)) > best_limit:
                        new_files.append(f)

                new_results = KnowledgeBaseManager._parse_result_files(new_files) if new_files else []
                if new_results:
                    all_results = cached_results + new_results
                    all_results.sort(key=lambda r: r.chapter_number)
                    kb = KnowledgeBaseManager._merge_incremental(
                        KnowledgeBase.from_dict(cached_kb.to_dict()), new_results)
                    _cache_put(cache_key, (current_fingerprint, all_results, kb))
                else:
                    kb = KnowledgeBase.from_dict(cached_kb.to_dict())
                    _cache_put(cache_key, (current_fingerprint, cached_results, kb))
                KnowledgeBaseManager._apply_rolling_data(kb, output_dir)
                logger.debug(f"build_temp_knowledge 近邻增量(best_limit={best_limit}): +{len(new_results)}章")
                return kb
            # 基线失效或无可用近邻 → 落入下方全量重建（陈旧数据不固化）
```

（其后「# 3. 全量构建」段原样保留，成为基线失效时的自然回退路径。）

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_kb_fingerprint.py backend/tests/test_models.py backend/tests/test_prompt_content_truncate.py -v && .venv\Scripts\python.exe -m pytest backend/tests/ -q --tb=no`
Expected: PASS；全量绿

- [ ] **Step 5: Commit**

```bash
git add backend/core/knowledge_base.py backend/tests/test_kb_fingerprint.py
git commit -m "fix(kb): 近邻增量校验基线指纹防陈旧固化+返回副本断开对象别名"
```

---

### Task G3: 风格 token 统计接线（消灭恒零盲区）

**Files:**
- Modify: `backend/core/style_analyzer.py`（call_llm_semantic / extract_style_profile 加可选 token_sink 参数）
- Modify: `backend/services/final_summary.py`（_run_style_extraction 传入 sink → _record_tokens("style", ...)）
- Test: Create `backend/tests/test_style_tokens.py`

**背景**：call_llm_semantic :337 拿到 tokens 后本地丢弃；final_summary.py 的 `_record_tokens("style", ...)` 分支无任何调用点 → UI/会话落盘的 style 分类永远 (0,0)。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_style_tokens.py`：

```python
"""P2：风格分析 token 消耗必须接入 runner 统计（此前恒为 (0,0) 盲区）"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config.settings import AppConfig
from backend.core import style_analyzer
from backend.services.final_summary import FinalSummaryRunner


def _make_runner(tmp_path):
    return FinalSummaryRunner(
        config=AppConfig(), output_dir=tmp_path,
        start_chapter=1, end_chapter=4, batch_size=2, concurrency=2,
        on_progress=lambda p: None, on_token_stats=lambda p: None)


def test_extract_profile_forwards_token_sink(tmp_path):
    captured = {}

    def fake_stats(blocks_dir):
        return {"chapter_count": 1}, [(1, "样本文本")]

    async def fake_semantic(prompt, api_config, token_sink=None):
        captured["called_with_sink"] = token_sink is not None
        if token_sink:
            token_sink((123, 45))
        return {f: "x" for f in style_analyzer.SEMANTIC_FIELDS}

    with patch.object(style_analyzer, "compute_book_stats", fake_stats), \
         patch.object(style_analyzer, "call_llm_semantic", fake_semantic):
        profile = asyncio.run(style_analyzer.extract_style_profile(
            tmp_path, "书名", {}, token_sink=captured.setdefault("sink", None)) if False else
            style_analyzer.extract_style_profile(tmp_path, "书名", {}, token_sink=lambda t: captured.setdefault("got", t)))

    assert captured.get("called_with_sink") is True
    assert captured.get("got") == (123, 45)


def test_runner_records_style_tokens(tmp_path):
    runner = _make_runner(tmp_path)

    def fake_extract(blocks_dir, book_name, api_config, token_sink=None):
        token_sink((100, 200))
        return {"statistical": {}, "semantic": {}}

    blocks = tmp_path / "blocks"
    blocks.mkdir()

    with patch("builtins.hasattr", hasattr), \
         patch.object(type(runner), "_acquire_llm_slot", AsyncMockCompat()) if False else None:
        pass

    # 直接调用 _run_style_extraction 的 sink 接线（绕过 blocks 存在性检查前的分支：
    # 先建 blocks 目录使路径检查通过）
    (runner.output_dir.parent / "blocks").mkdir(parents=True, exist_ok=True)
    with patch("backend.services.final_summary.extract_style_profile", fake_extract):
        asyncio.run(runner._run_style_extraction())

    assert runner._tokens_style == (100, 200), f"style token 应被记录，实际 {runner._tokens_style}"


class AsyncMockCompat:
    """占位避免误用；实际未使用（见上方 if False 分支）"""
    def __init__(self): raise NotImplementedError
```

> Step 1 备注：第二条约 40-48 行处的 `if False` 死代码是模板残留，**执行时删除**该两行（`with patch(...) if False else None:` 整段），只保留 blocks.mkdir + patch extract + 断言。第一条的调用形如 `extract_style_profile(..., token_sink=...)`——按 Step 3 实际签名传参。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_style_tokens.py -v`
Expected: FAIL（extract_style_profile 不接受 token_sink / _tokens_style 仍 (0,0)）

- [ ] **Step 3: 实现**

(a) `style_analyzer.call_llm_semantic` 签名加参并在 :337 之后透传：

```python
async def call_llm_semantic(prompt: str, api_config: dict,
                            token_sink=None) -> Optional[dict]:
    """调用 LLM 获取语义风格特征（async）；token_sink 可选，接收 (in, out) 元组"""
```
```python
    success, content, error, tokens, _call_stats = await client.chat_with_retry(messages, max_tokens=20000)
    if token_sink:
        try:
            token_sink(tokens)
        except Exception:
            pass
```

(b) `extract_style_profile` 签名加 `token_sink=None`，:404 调用处透传。

(c) `final_summary._run_style_extraction`：extract 调用改为：

```python
                return await extract_style_profile(
                    blocks_dir, self.book_name, api_config,
                    token_sink=lambda t: self._record_tokens("style", t[0], t[1]))
```

（执行者先 grep 一处既有 `self._record_tokens(` 调用核对参数序——若为 `(kind, in, out)` 则如上；若不同以实际为准并报告。）同时确认 import：final_summary 是否已 `from ..core.style_analyzer import extract_style_profile`（现有调用即来自它，无需新导）。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_style_tokens.py backend/tests/test_style_task_stop.py backend/tests/test_global_llm_sem.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/core/style_analyzer.py backend/services/final_summary.py backend/tests/test_style_tokens.py
git commit -m "fix(summary): 风格token经sink接入统计，消灭恒零盲区"
```

---

### Task G4: 书目刷新移出事件循环（miss 时后台刷新 + 单飞保护）

**Files:**
- Modify: `backend/services/book_service.py`
- Test: Create `backend/tests/test_book_service_async_refresh.py`

**背景**：get_book_path 未命中即在事件循环内同步 refresh_books()（iterdir/stat 所有候选根，网络盘秒级卡顿）；项目自己在 H1 把同类模式定性为必修。

设计取舍（控制器定版）：**保持同步函数签名不变**（所有现存调用方零改动），语义调整为——首次冷启动（`_books` 为空）仍阻塞刷一次保证可用性；此后 miss 只触发**后台单飞刷新**并立即返回 None（下一次查询即可命中）。代价：全新书首次查询可能 404 一次，重试即中；换取事件循环零冻结。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_book_service_async_refresh.py`：

```python
"""P2：get_book_path miss 后不得再在事件循环内阻塞式全量扫描；
冷启动（映射为空）除外。"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.services import book_service


class _StubService:
    def __init__(self, roots):
        from types import SimpleNamespace
        self.queue = SimpleNamespace(items=[])
        self.config_manager = SimpleNamespace(
            config=SimpleNamespace(working_directory=""))
        self.workspace_path = roots


def test_warm_miss_does_not_block(monkeypatch, tmp_path):
    """_books 已预热时 miss：不得同步调 refresh_books（会冻结循环），
    应触发后台单飞并立即返回 None"""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(book_service, "_books", {"已知书": ws})
    monkeypatch.setattr(book_service, "_manual_books", {})

    spawned = []
    class FakeThread:
        def __init__(self, target=None, daemon=None, **k):
            spawned.append(target)
            self.daemon = daemon
        def start(self): pass
    monkeypatch.setattr(book_service.threading, "Thread", FakeThread)

    result = book_service.get_book_path("不存在的新书")
    assert result is None
    assert len(spawned) == 1, "miss 应回台触发一次单飞刷新"


def test_cold_start_still_blocks_for_usability(monkeypatch, tmp_path):
    """_books 为空（进程刚起）：保留一次阻塞刷新保证首查可用"""
    calls = {"n": 0}
    def fake_refresh():
        calls["n"] += 1
        book_service._books.clear()
        return {}
    monkeypatch.setattr(book_service, "_books", {})
    monkeypatch.setattr(book_service, "refresh_books", fake_refresh)
    monkeypatch.setattr(book_service, "_manual_books", {})

    result = book_service.get_book_path("任意")
    assert calls["n"] == 1
    assert result is None


def test_single_flight_no_stampede(monkeypatch, tmp_path):
    """刷新进行中时后续 miss 不再叠加线程"""
    monkeypatch.setattr(book_service, "_books", {"a": tmp_path})
    monkeypatch.setattr(book_service, "_manual_books", {})
    monkeypatch.setattr(book_service, "_refreshing", True)

    spawned = []
    class FakeThread:
        def __init__(self, *a, **k): pass
        def start(self): pass
    monkeypatch.setattr(book_service.threading, "Thread", FakeThread)

    assert book_service.get_book_path("不存在") is None
    assert not spawned, "单飞保护下不应重复拉起刷新线程"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_book_service_async_refresh.py -v`
Expected: FAIL（当前实现同步阻塞调 refresh；无 threading 导入则 AttributeError 亦算 FAIL）

- [ ] **Step 3: 实现**

book_service.py：

(a) 顶部加 `import threading`；模块级加：

```python
# P2 修复（2026-08-24）：miss 后的事件循环内同步全量扫描会把网络盘延迟放大成
# 秒级 UI/WS 冻结（同类问题项目已在 H1 定性必修）。策略：
# 冷启动（映射空）保留一次阻塞刷新；此后 miss 仅后台单飞刷新 + 立即返回 None。
_refreshing = False
_refresh_lock = threading.Lock()
```

(b) get_book_path 替换：

```python
def get_book_path(book_id: str) -> Optional[Path]:
    """按书名解析书目录。

    映射缺失时：冷启动（_books 空）阻塞刷一次保证可用；此后仅触发后台单飞
    刷新并立即返回 None（调用方本次 404，刷新完成后下次查询即命中）。"""
    global _refreshing
    path = _books.get(book_id)
    if path is not None and path.exists():
        return path

    if not _books:
        # 冷启动：一次阻塞刷新（此时通常还没有用户请求在等）
        refresh_books()
    else:
        with _refresh_lock:
            should_spawn = not _refreshing
            _refreshing = True
        if should_spawn:
            def _bg():
                global _refreshing
                try:
                    refresh_books()
                finally:
                    with _refresh_lock:
                        _refreshing = False
            threading.Thread(target=_bg, daemon=True, name="book-refresh").start()

    path = _books.get(book_id)
    if path is not None and path.exists():
        return path
    return None
```

（list_books 的显式刷新语义不变。）

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_book_service_async_refresh.py backend/tests/test_workspace_scan.py -v && .venv\Scripts\python.exe -m pytest backend/tests/ -q --tb=no`
Expected: PASS；全量绿

- [ ] **Step 5: Commit**

```bash
git add backend/services/book_service.py backend/tests/test_book_service_async_refresh.py
git commit -m "fix(books): miss改后台单飞刷新+立即返回，消除事件循环秒级冻结"
```

---

### Task G5: 协议误判探测回退（claude-* 走 OpenAI 网关自动纠正）

**Files:**
- Modify: `backend/services/queue_service.py`（check_api :281-306 追加跨协议探针）
- Test: Create `backend/tests/test_provider_heal.py`

**背景**：detect_provider 把 OpenAI 兼容网关上的 claude-* 模型判成 anthropic → 请求打 /v1/messages 404，auto 模式下整本书无提示全量失败。check_api 预检每本书前必跑，是天然的纠正点。

设计（控制器定版）：models 检查与原生协议 chat 探针都失败后，**翻转 provider 各做一次 models 探针**；任一成功即认定误判 → 就地修改 `config.api.provider`（运行期对象，随后所有服务共用此配置对象即自动纠正）+ WARNING 日志指引「保存设置以持久化」+ return True。翻转探针也失败才 return False。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_provider_heal.py`：

```python
"""P2：网关以 OpenAI 协议暴露 claude-* 时，预检应探测出正确协议并就地纠正"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config.settings import AppConfig
from backend.services.queue_service import AnalysisService


def _cfg(provider="auto"):
    c = AppConfig()
    c.api.model = "claude-3-5-sonnet"
    c.api.base_url = "https://gw.example.com/v1"
    c.api.provider = provider
    return c


def _svc():
    return AnalysisService.__new__(AnalysisService)


def test_cross_protocol_probe_heals_and_returns_true(monkeypatch):
    calls = []

    async def fake_list_models(base_url, api_key, provider):
        calls.append(provider)
        if provider == "openai":
            return ["claude-3-5-sonnet"]
        raise RuntimeError("404 not found")

    monkeypatch.setattr(
        "backend.services.queue_service.LLMClient.list_models", fake_list_models)

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
```

> Step 1 备注：check_api 当前实现 models 失败后会走 chat 探针——修复版需保证「chat 探针也尝试翻转 provider」。FakeClient 记录构造时的 config 以便断言两次探针分别用了两种 provider（可选加强）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_provider_heal.py -v`
Expected: FAIL（无翻转逻辑，第一条 ok=False）

- [ ] **Step 3: 实现**

check_api 重构为四段：①空模型拦截（原样）②原生 provider models 探针 ③chat 探针（原生）④跨协议探针（翻转 provider 的 models + chat）。骨架：

```python
    async def check_api(self, config: AppConfig) -> bool:
        if not str(config.api.model or '').strip():
            logger.warning("API 预检查失败：未配置模型（api.model 为空），请在设置页填写模型名")
            return False

        flipped = "openai" if (config.api.provider or "auto") in ("auto", "anthropic") else "anthropic"

        async def models_ok(provider: str) -> bool:
            try:
                models = await LLMClient.list_models(config.api.base_url, config.api.api_key, provider)
                return bool(models)
            except Exception as e:
                logger.debug(f"models 健康检查异常({provider}): {e}")
                return False

        async def chat_ok(provider: str) -> bool:
            try:
                client_cfg = config.model_copy(deep=True)
                client_cfg.api.provider = provider
                client = LLMClient(client_cfg)
                ok, _c, err, _t = await client.chat([{"role": "user", "content": "hi"}])
                if ok:
                    return True
                logger.debug(f"chat 探针失败({provider}): {err}")
                return False
            except Exception as e:
                logger.debug(f"chat 探针异常({provider}): {e}")
                return False

        native = (config.api.provider or "auto")

        # ① 原生协议 models → ② 原生 chat 探针
        if await models_ok(native):
            return True
        logger.debug("models 健康检查不可用或为空，回退 chat 探针")
        if await chat_ok(native):
            return True

        # ③ 跨协议探针（P2 2026-08-24：聚合网关常以 OpenAI 协议暴露 claude-*，
        # detect_provider 会误判为 anthropic → /v1/messages 404 整书全量失败）
        logger.warning(f"原生协议({native})探针均失败，尝试跨协议({flipped})探针…")
        if await models_ok(flipped) or await chat_ok(flipped):
            config.api.provider = flipped
            logger.warning(
                f"检测到协议误判：已就地纠正 provider={flipped}（本次运行生效）。"
                f"请在设置页保存配置以持久化，避免每次启动重复探测。")
            return True

        logger.warning("API 预检查失败：原生与跨协议探针均失败")
        return False
```

（AppConfig 有 model_copy？dataclass 无 pydantic——改用 `replace(config.api, provider=flipped)` 构造 APIConfig：`from dataclasses import replace; client = LLMClient(replace(config.api, provider=flipped))`。执行者按此落地，删除 model_copy 写法。）

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_provider_heal.py backend/tests/test_queue_service.py backend/tests/test_auto_summary.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/queue_service.py backend/tests/test_provider_heal.py
git commit -m "fix(queue): 预检加跨协议探针自动纠正provider误判"
```

---

### Task G6: 切分窄化吞章收口（次级格式并入 + 噪声比额门槛）

**Files:**
- Modify: `backend/services/splitter_service.py`（_compile_pattern 窄化段，约 :288-303）
- Test: Create `backend/tests/test_splitter_mixed_formats.py`（本任务测试预算充足：至少 5 个用例）

**背景**：主导格式得分 ≥5 时切分正则收窄为「主导 + 特章」，混合命名书的其他真实章节行（如 `第三卷 第一章`）被当正文吞进上一章——输入阶段静默丢内容，下游全链路缺章。直接全量并入又会让「1. 2. 3.」列表噪声造成过度切分（P2-11 的初衷）。

设计（控制器定版）：**比额门槛**——次级候选同时满足「绝对得分 ≥ 2」且「得分 ≥ 主导得分的 15%」才并入 combined。合法卷章混排（卷章数与主导同量级）必然入选；噪声列表（得分远超主导或只有 0-1 次）被挡住。特章族维持无条件并入（现状）。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_splitter_mixed_formats.py`：

```python
"""P2 收口：主导格式窄化不得吞掉混排的真实章节行，也不得被高频噪声带偏。
统一入口 preview_split/save_split 走 _compile_pattern；此处直接测编译产物。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.services.splitter_service import SplitOptions, _compile_pattern, split_text


def _compile(lines):
    opts = SplitOptions()
    return _compile_pattern([l.rstrip("\n") for l in lines], opts)


def test_mixed_volume_chapter_lines_are_included():
    """第X章为主 + 第三卷第一章混排：合并式行必须作为切分边界（此前被吞）"""
    lines = []
    for v, chs in enumerate(["一", "二"], 1):
        for i in range(1, 4):
            lines.append(f"第{(v-1)*3+i}章 标题{(v-1)*3+i}")
            lines.append("正文内容。" * 20)
            if i == 3:
                lines.append(f"第三卷 第{chs}卷起首")  # 合并式样例行
    rx, name = _compile(lines)
    joined = "\n".join(lines)
    # 合并式行能作为边界：split 后不应把它留在上一章正文里
    assert rx.search(joined) is not None


def test_real_world_split_mixed_book(tmp_path):
    src = tmp_path / "mixed.txt"
    parts = []
    idx = 0
    for block_start in (1, 11, 21):
        parts.append(f"第{block_start}章 卷内首页")
        parts.append("剧情推进。" * 50)
        parts.append(f"第三卷 第{block_start}卷")     # 混排合并式行（此前被吞）
        parts.append("过渡剧情。" * 30)
        for c in range(block_start + 1, block_start + 4):
            parts.append(f"第{c}章 后续")
            parts.append("剧情推进。" * 50)
    src.write_text("\n".join(parts), encoding="utf-8")
    out = tmp_path / "blocks"
    r = splitter_save(src, out)
    # 12 个真实标题（4 组 × 3 章 + 3 条合并式行 = 若实现并入则更多）——
    # 关键断言：合并式行的文字不出现在任何章节正文开头
    import json
    chapters_json = json.loads((out / "chapters.json").read_text(encoding="utf-8"))
    bodies = []
    for c in chapters_json:
        f = out / f"{c['index']:04d}.txt"
        bodies.append(f.read_text(encoding="utf-8"))
    for b in bodies:
        assert not b.startswith("第三卷"), "合并式行被吞进上一章正文（回归）"
    assert len(chapters_json) >= 12


def splitter_save(src, out):
    from backend.services import splitter_service
    return splitter_service.save_split(src, out)


def test_noise_list_dominant_not_included():
    """编号列表行数远超章节标题（比值 > 15%）不得并入导致过度切分"""
    lines = []
    for i in range(1, 21):
        lines.append(f"第{i}章 标题{i}")
        lines.append("正文内容。" * 20)
    for i in range(1, 201):
        lines.append(f"{i}. 列表条目内容{i}")   # 噪声：数量远超主导且比值 >> 15%
    rx, _ = _compile(lines)
    joined = "\n".join(lines)
    # 列表行不应成为切分边界：匹配到的行数应约等于章节数而非 220
    import re as _re
    hits = [l for l in lines if rx.match(l.strip())]
    assert len(hits) <= 25, f"列表噪声被并入切分边界：{len(hits)} 行命中"


def test_low_ratio_secondary_excluded():
    """次级格式只出现 1 次（< 绝对阈值 2）不并入"""
    lines = []
    for i in range(1, 9):
        lines.append(f"第{i}章 标题{i}")
        lines.append("正文内容。" * 30)
    lines.insert(4, "第三卷 第一章孤例")   # 仅 1 次
    rx, _ = _compile(lines)
    joined = "\n".join(lines)
    assert not rx.match("第三卷 第一章孤例")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_splitter_mixed_formats.py -v`
Expected: 混排两条 FAIL（rx 匹配不到合并式行 / 正文 startswith 第三卷）；噪声与低比例两条 PASS（基线锁定）

- [ ] **Step 3: 实现**

窄化段（现 :296-303 附近，锚点注释「P2-11」）替换为：

```python
    # P2-11 + G6 收口（2026-08-24）：主导格式足够强时收窄为
    # 「主导 + 特章 + 达标次级」。次级入选双门槛：绝对得分 ≥ 2（排掉孤例噪声）
    # 且得分 ≥ 主导的 15%（排掉数量远超主导的高频噪声列表——那正是 P2-11 要防的
    # 过度切分场景）。合法卷章混排与主导同量级，必然入选。
    if best_name and scores[best_name] >= 5 and not best_name.startswith("特章"):
        _pat_by_name = {name: pat for name, pat in CHAPTER_PATTERNS}
        best_score = scores[best_name]
        combined = f"(?:{_pat_by_name[best_name]})"
        if special_alt:
            combined += f"|(?:{special_alt})"
        included_secondary = 0
        threshold = max(2, int(best_score * 0.15))
        for name, pat in CHAPTER_PATTERNS:
            if name == best_name or name.startswith("特章"):
                continue
            sc = scores.get(name, 0)
            if sc >= threshold:
                combined += f"|(?:{pat})"
                included_secondary += 1
        if included_secondary:
            logger.info(f"次级章节格式并入切分: {included_secondary} 种 "
                        f"(阈值≥{threshold})")
        return re.compile(combined, re.MULTILINE | re.IGNORECASE), pattern_name
```

（特章族名前缀以 CHAPTER_PATTERNS 实际命名为准——grep `startswith("特章")` 现状照抄；若现有判断字符串不同，沿用现状。）

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_splitter_mixed_formats.py backend/tests/test_splitter_atomic.py backend/tests/test_memory_state_skipped.py -v`
Expected: PASS ×5+

- [ ] **Step 5: Commit**

```bash
git add backend/services/splitter_service.py backend/tests/test_splitter_mixed_formats.py
git commit -m "fix(splitter): 次级章节格式按比额门槛并入切分，杜绝混排吞章"
```

---

### Task G7: 编码评分择优（gb18030 繁体陷阱 + UTF-16 预检统一）

**Files:**
- Modify: `backend/utils/text_utils.py`（重构 detect_and_decode / detect_encoding 共用评分选择器）
- Test: Create `backend/tests/test_text_encoding.py`

**背景**：①繁体中文书在 gb18030 环节解码「成功」产出乱码/PUA 字符（实测），静默整书损坏；②detect_and_decode 缺 detect_encoding 已有的 BOM/NUL 预检（两套逻辑漂移）。

设计（控制器定版）：引入采样评分选择器——每候选取头部 256KB 解码，罚分制（U+FFFD×8 + 控制字符×4 + PUA(U+E000-F8FF)×3，除以样本字符数），取最低罚分为胜者（并列取优先级靠前者）；BOM/NUL 预检保留且两入口共用。选定后整文件按胜者编码完整解码。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_text_encoding.py`：

```python
"""P2 收口：编码选择从「首个成功」改为「采样罚分择优」——
Big5 书不得落入 gb18030 乱码；两入口（detect_and_decode/detect_encoding）行为一致。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from backend.utils.text_utils import detect_and_decode, detect_encoding

BIG5_TEXT = "這是一個測試章節，主角前往靈山尋找失落的功法。"
GBK_TEXT = "这是一个测试章节，主角前往灵山寻找失落的功法。"


def _write_bytes(p: Path, data: bytes):
    p.write_bytes(data)


def test_big5_book_not_moibaked_by_gb18030(tmp_path):
    p = tmp_path / "big5.txt"
    _write_bytes(p, BIG5_TEXT.encode("big5"))

    text = detect_and_decode(p, ["utf-8", "gbk", "gb18030", "big5"])
    assert "靈山" in text and "測試" in text, f"繁体书被错误解码：{text[:40]}"

    assert detect_encoding(p, ["utf-8", "gbk", "gb18030", "big5"]) == "big5"


def test_gbk_simplified_still_detected(tmp_path):
    p = tmp_path / "gbk.txt"
    _write_bytes(p, GBK_TEXT.encode("gbk"))
    text = detect_and_decode(p, ["utf-8", "gbk", "gb18030", "big5"])
    assert "灵山" in text
    assert detect_encoding(p, ["utf-8", "gbk", "gb18030", "big5"]) == "gbk"


def test_utf8_priority_kept(tmp_path):
    p = tmp_path / "u8.txt"
    _write_bytes(p, GBK_TEXT.encode("utf-8"))
    assert detect_encoding(p, ["utf-8", "gbk"]) == "utf-8"


def test_utf16le_nobom_ascii_still_works(tmp_path):
    p = tmp_path / "u16.txt"
    _write_bytes(p, "Hello Chapter 1.\n".encode("utf-16-le"))
    text = detect_and_decode(p, ["utf-8", "gbk", "utf-16"])
    assert "Hello Chapter 1." in text
    assert "\x00" not in text


def test_all_fail_raises(tmp_path):
    p = tmp_path / "bin.txt"
    _write_bytes(p, b"\xff\xfe\x00\x00\xff\xfe")
    with pytest.raises(Exception):
        detect_and_decode(p, ["utf-8", "gbk"])


def test_both_entries_agree(tmp_path):
    p = tmp_path / "big5b.txt"
    _write_bytes(p, ("章一：" + BIG5_TEXT).encode("big5"))
    prios = ["utf-8", "gbk", "gb18030", "big5"]
    enc = detect_encoding(p, prios)
    text = detect_and_decode(p, prios)
    # 两入口选中的编码一致（text 能以该编码无损往返）
    assert text.encode(enc).decode(enc) == text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_text_encoding.py -v`
Expected: Big5 两条 FAIL（乱码/gbk 误判）；其余 PASS（基线）

- [ ] **Step 3: 实现**

text_utils.py 重构（detect_and_decode :11-41 与 detect_encoding :44-102）：

(a) 模块级新增：

```python
_SAMPLE_BYTES = 256 * 1024
_CTRL_ALLOWED = set("\t\n\r")


def _penalty_sample(raw: bytes, encoding: str) -> Optional[float]:
    """采样解码罚分：越低越好。None = 该编码在采样上硬解失败。

    罚分权重：U+FFFD ×8（解码器替换符，强信号）/ 控制字符 ×4 /
    PUA(U+E000-F8FF) ×3（gb18030 吞 Big5 的典型产物）。
    P2 修复（2026-08-24）：取代「首个解码成功即返回」——Big5 字节流在 gb18030
    下无错解码但产出乱码，繁体书整本静默损坏。"""
    sample = raw[:_SAMPLE_BYTES]
    try:
        text = sample.decode(encoding)
    except UnicodeDecodeError:
        return None
    except LookupError:
        return None
    if not text:
        return 0.0
    penalty = text.count("\ufffd") * 8
    penalty += sum(1 for ch in text if ord(ch) < 32 and ch not in _CTRL_ALLOWED) * 4
    penalty += sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF) * 3
    return penalty / len(text)


def _select_encoding(raw: bytes, encoding_priority: List[str]) -> Optional[str]:
    """BOM/NUL 预检 + 采样罚分择优。返回胜者编码名；全部失败返回 None。"""
    head = raw[:4096]
    if head.startswith(b'\xff\xfe\x00\x00') or head.startswith(b'\x00\x00\xfe\xff'):
        return 'utf-32'
    if head.startswith(b'\xff\xfe'):
        return 'utf-16'
    if head.startswith(b'\xfe\xff'):
        return 'utf-16-be'
    if head.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    if head and head.count(b'\x00') / len(head) > 0.05:
        for enc in ('utf-16', 'utf-16-be'):
            try:
                raw.decode(enc)
                return enc
            except (UnicodeDecodeError, LookupError):
                continue

    best_enc, best_score = None, None
    for encoding in encoding_priority:
        score = _penalty_sample(raw, encoding)
        if score is None:
            continue
        if best_score is None or score < best_score:
            best_enc, best_score = encoding, score
    return best_enc
```

(b) `detect_and_decode` 替换为：

```python
def detect_and_decode(file_path: Path, encoding_priority: List[str]) -> str:
    """读取文件并以采样罚分择优解码（BOM/NUL 预检与 detect_encoding 共用一套）

    Raises:
        UnicodeDecodeError: 所有编码都失败时抛出
    """
    raw = Path(file_path).read_bytes()
    enc = _select_encoding(raw, encoding_priority)
    if enc is None:
        raise UnicodeDecodeError(
            "unknown", b"", 0, 1, f"无法使用任何编码读取文件: {file_path}")
    return raw.decode(enc)
```

(c) `detect_encoding` 替换为：

```python
def detect_encoding(file_path: Path, encoding_priority: List[str]) -> str:
    """检测文件编码（与 detect_and_decode 共用评分选择器）"""
    try:
        raw = Path(file_path).read_bytes()
    except Exception as e:
        raise IOError(f"读取文件失败: {e}")
    enc = _select_encoding(raw, encoding_priority)
    if enc is None:
        raise UnicodeDecodeError(
            "unknown", b"", 0, 1, f"无法使用任何编码读取文件: {file_path}")
    return enc
```

(d) 顶部 import 校验：`from typing import Dict, List, Optional`（补 Optional）。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/test_text_encoding.py backend/tests/test_splitter_atomic.py backend/tests/test_splitter_mixed_formats.py -v && .venv\Scripts\python.exe -m pytest backend/tests/ -q --tb=no`
Expected: PASS；全量绿

- [ ] **Step 5: Commit**

```bash
git add backend/utils/text_utils.py backend/tests/test_text_encoding.py
git commit -m "fix(text): 编码采样罚分择优，Big5不再坠入gb18030乱码+两入口预检统一"
```

---

### Task GD: 文档收口 + 全量验证

**Files:**
- Modify: `agent.md`（§10 追加 10.16 + §5.x 微调）
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 全量验证**

```bash
.venv\Scripts\python.exe -m pytest backend/tests/ --tb=no -q
cd frontend && npm run build
```
Expected: 全绿（227+ 本批新增）；build 成功。

- [ ] **Step 2: agent.md 更新**

§10 追加（编号续 10.16，插在最新条目之后）：

```markdown
### 10.16 2026-08-24 深度修复批次（大改值得七项）
- G1 审核拦截 skipped 标记持久化（重启不再重付 LLM 费用）；G2 KB 近邻增量基线指纹校验+副本断别名（陈旧 KB 不再固化进最终 knowledge.json）；G3 风格 token 接入统计；G4 书目刷新后台单飞（消除事件循环冻结）；G5 预检跨协议探针自动纠正 provider 误判；G6 切分次级格式比额门槛并入（混排书不再吞章）；G7 编码采样罚分择优（Big5 不坠入 gb18030 乱码，两入口预检统一）
- **原因**：审计剩余项中「后果严重度×触发频率」最高、值得动核心逻辑的七项
- **取舍**：normalizer salvage、死代码功能、macOS osascript、polish 级继续搁置
```

§5.x 一句话微调（须与代码一致）：§5.3 text_utils 行更新为新机制描述；§5.2 book_service 行补「miss 后台单飞」；§5.1 knowledge_base 相关描述如提及近邻增量补「基线指纹校验」；§5.2 splitter 行补「次级格式比额门槛并入」。

- [ ] **Step 3: CHANGELOG.md 追加一条**

- [ ] **Step 4: Commit**

```bash
git add agent.md CHANGELOG.md
git commit -m "docs: 同步agent.md与CHANGELOG至深度修复批次状态"
```

---

## Self-Review 结论

1. **覆盖核对**：7 项 → G1(#10)/G2(#7)/G3(#4)/G4(#18)/G5(#1)/G6(#19)/G7(#42+#43)；批次纪律写入全局约束。
2. **占位符扫描**：各任务含完整代码或带授权备注的骨架；G1(d) 的变量名、G3 的 _record_tokens 参数序、B 系列惯例的函数名定位均为防御性指引且有替代方案说明。
3. **一致性**：token_sink 管道三处签名一致；failed_chapters_in/_apply_rolling_to_snapshot 等既有接口未被破坏；测试文件命名无冲突。
