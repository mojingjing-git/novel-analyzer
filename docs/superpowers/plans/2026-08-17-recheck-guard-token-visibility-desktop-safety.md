# 复检超限保护 + Token 可见性 + 桌面安全 + 预览参数 + 工程卫生 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在"零召回风险"约束下为全书伏笔复检加上下文超限保护并把复检批大小配置化；分析/总结 token 统计落盘并在前端可见；桌面端加单实例锁与关闭确认；Prompt 预览支持指定章节；清理仓库卫生死角并同步 agent.md。

**Architecture:** 全部在现有模块内做增量改动，不新增依赖。复检保护复用现有 `_run_global_foreshadow_recheck` 结构，新增纯方法 `_plan_recheck_batches` 便于单测；token 落盘复用 `safe_save_json` 原子写；单实例锁用"锁文件 + 健康检查"判定活实例（不依赖 PID 存活检测的跨平台坑）；关闭确认同时覆盖 OS 关窗（`events.closing`  veto）与自定义标题栏按钮（`Api.close` 先确认后 destroy）。

**Tech Stack:** Python 3.11+ / FastAPI / pytest；Vue 3.5 + TS（`--win-*` 变量 + `glass-*` 类）；pywebview。

## Global Constraints

- **禁止任何 git 操作**：不 commit、不 add、不 stash（仓库有 26 个未提交的 Win11 前端重做文件，绝不能卷入）。
- **禁止批量转换行尾符**（CRLF/LF 保持原样）；查看真实改动用 `git diff -w -- <file>`。
- **禁止打印/提交 `config.json` 内容**（含真实 API Key）。
- 终端一律用 `pwsh`（PowerShell 7），不用 Windows PowerShell 5.1。
- Python 解释器优先用 `F:\AI\小说分析器\.venv\Scripts\python.exe`；pytest 命令：`.venv\Scripts\python.exe -m pytest backend/tests/ -x -q`。
- 前端改动必须遵守 Win11 设计系统：颜色/圆角/间距用 `--win-*` 变量，输入框用 `glass-input` 类；禁止 `backdrop-filter`。
- 前端构建：`F:` 是共享盘，esbuild 有限制。先直接试 `npm run build`（frontend 目录）；若遇 esbuild 网络盘报错，改用 `cmd /c "build_frontend.bat < nul"`（项目根目录）。
- 修改完成后由**最后一个任务**统一更新 agent.md；中间任务不动 agent.md。
- 全程用中文日志/注释，风格与所在文件一致。

---

### Task 1: 复检上下文超限保护 + 复检批大小配置化（后端）

**Files:**
- Modify: `backend/config/constants.py`（约 :32 附近加常量）
- Modify: `backend/config/settings.py`（AnalysisConfig，:117-118 附近加字段）
- Modify: `backend/services/final_summary.py`（:1208-1258 复检方法 + :1459-1466 调用点）
- Test: `backend/tests/` 下找到现有 final_summary 测试文件（先 `glob backend/tests/test_*summary*` / `grep FinalSummaryRunner backend/tests`）；若 fixture 可复用则加进去，否则新建 `backend/tests/test_recheck_guard.py`

**Interfaces:**
- Consumes: 现有 `GLOBAL_RECHECK_USER_TEMPLATE`、`ForeshadowItem`、`safe_save_json`；常量命名必须按下文，Task 2 的前端 DTO 依赖字段名 `foreshadow_recheck_batch_size`。
- Produces: `AnalysisConfig.foreshadow_recheck_batch_size: int`（默认 40）；`FinalSummaryRunner._plan_recheck_batches(recheck_items, full_text, volume_summaries, volume_ranges) -> List[Tuple[list, str]]`；常量 `RECHECK_FULLTEXT_BUDGET_CHARS = 150000`。

- [ ] **Step 1: 写失败测试**

新建或追加测试（构造 runner 的方式参考现有 final_summary 测试；最小可用构造：`FinalSummaryRunner(config=AppConfig(), output_dir=tmp_path, start_chapter=1, end_chapter=99999, batch_size=30, concurrency=1, on_progress=lambda p: None, on_token_stats=None)`）：

```python
def _mk_volumes(n, chars):
    """n 卷，每卷 chars 字符；返回 (volume_summaries, volume_ranges)，每卷 30 章"""
    vols, ranges = [], []
    for i in range(n):
        cs, ce = i * 30 + 1, (i + 1) * 30
        vols.append(f"=== 第{cs}-{ce}章 ===\n" + "x" * chars)
        ranges.append((cs, ce))
    return vols, ranges

def _mk_items(first_seens):
    return [{"id": f"fs_{i:03d}", "clue": f"伏笔{i}", "first_seen": fs,
             "last_seen": fs, "evidence_chapters": [fs]}
            for i, fs in enumerate(first_seens)]

def test_plan_recheck_under_budget_keeps_full_text():
    runner = ...  # 见上方构造
    vols, ranges = _mk_volumes(2, 1000)
    items = _mk_items([5, 40])
    plans = runner._plan_recheck_batches(items, "\n\n".join(vols), vols, ranges)
    assert len(plans) == 1
    assert plans[0][0] == items
    assert "第1-30章" in plans[0][1] and "第31-60章" in plans[0][1]

def test_plan_recheck_over_budget_drops_pre_burial_volumes():
    """10 卷×2 万字符=20 万>预算 15 万；伏笔全埋在后段→砍掉 ch_end < 组内最早 first_seen 的卷"""
    runner = ...
    vols, ranges = _mk_volumes(10, 20000)
    items = _mk_items([250, 260, 270, 280, 290, 295])
    plans = runner._plan_recheck_batches(items, "\n\n".join(vols), vols, ranges)
    assert plans, "砍卷后应有可用计划"
    for _items, text in plans:
        assert "第1-30章" not in text          # 埋设前的卷已被砍掉
        assert "第271-300章" in text          # 埋设点所在的卷必须保留
        assert len(text) <= 150000

def test_plan_recheck_still_over_budget_skips():
    """单卷就超预算且伏笔埋在第 1 章→无卷可砍→整组跳过（返回空），不调用 LLM 烧钱"""
    runner = ...
    vols, ranges = _mk_volumes(1, 200000)
    plans = runner._plan_recheck_batches(_mk_items([1]), vols[0], vols, ranges)
    assert plans == []

def test_plan_recheck_over_budget_without_ranges_skips():
    """无卷范围信息且超预算→跳过（返回空），不裸调 LLM 赌 400"""
    runner = ...
    vols, _ = _mk_volumes(1, 200000)
    plans = runner._plan_recheck_batches(_mk_items([1]), vols[0], vols, None)
    assert plans == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/ -k plan_recheck -x -q`
Expected: FAIL（`AttributeError: 'FinalSummaryRunner' object has no attribute '_plan_recheck_batches'`）

- [ ] **Step 3: 实现**

3a. `backend/config/constants.py` 在 `DEFAULT_VOLUME_COMPRESS_GROUP`（:29）后加：

```python
RECHECK_FULLTEXT_BUDGET_CHARS = 150000  # 全书伏笔复检的卷摘要字符预算（≈10万token，给输出留余量）；超出按伏笔埋设章砍"埋设前的卷"降级（召回无损：回收必发生在埋设之后）
DEFAULT_FORESHADOW_RECHECK_BATCH_SIZE = 40  # 全书伏笔复检每次 LLM 调用携带的活跃伏笔数
```

3b. `backend/config/settings.py` AnalysisConfig 在 `summary_batch_size`（:118）后加：

```python
    foreshadow_recheck_batch_size: int = DEFAULT_FORESHADOW_RECHECK_BATCH_SIZE  # 全书伏笔复检批大小；调大减少调用次数（省 cache 命中价前缀），单批过大可能稀释注意力
```

并在文件顶部 from .constants import 列表中补 `DEFAULT_FORESHADOW_RECHECK_BATCH_SIZE`（照现有导入格式）。

3c. `backend/services/final_summary.py`：

- 顶部 import 区确认有 `from typing import ... Tuple ...`（没有则补）；import 区补 `RECHECK_FULLTEXT_BUDGET_CHARS` 的来源导入（照该文件现有 constants 导入行追加）。
- `_run_global_foreshadow_recheck` 签名改为：

```python
    async def _run_global_foreshadow_recheck(self, volume_summaries: List[str],
                                             foreshadow_catalog: list = None,
                                             volume_ranges: List[Tuple[int, int]] = None) -> None:
```

- 新增纯方法（放在 `_run_global_foreshadow_recheck` 之前）：

```python
    def _plan_recheck_batches(self, recheck_items: list, full_text: str,
                              volume_summaries: List[str],
                              volume_ranges: List[Tuple[int, int]] = None) -> List[Tuple[list, str]]:
        """规划复检上下文：返回 [(伏笔子集, 该子集使用的卷摘要文本)]。
        未超预算 → [(全部, 全文)]（2026-08-07 决策的原行为，KV cache 友好）；
        超预算 → 按 first_seen 升序切 ≤3 个连续组，每组砍掉"ch_end < 组内最早埋设章"的卷
        （召回无损：回收必发生在埋设之后）；砍后仍超预算的组丢弃并记 error，不调用 LLM。"""
        budget = RECHECK_FULLTEXT_BUDGET_CHARS
        if len(full_text) <= budget:
            return [(recheck_items, full_text)]
        if not volume_ranges:
            logger.error(f"复检卷摘要超预算（{len(full_text)}>{budget}）且无卷章范围信息，跳过全书复检")
            return []
        sorted_items = sorted(recheck_items, key=lambda c: c.get("first_seen", 0))
        n = len(sorted_items)
        per = (n + 2) // 3  # ≤3 组，保住组间卷摘要前缀一致（KV cache）
        plans: List[Tuple[list, str]] = []
        for gstart in range(0, n, per):
            group = sorted_items[gstart:gstart + per]
            min_first = min(c.get("first_seen", 0) for c in group)
            kept = [i for i, (_cs, ce) in enumerate(volume_ranges) if ce >= min_first]
            group_text = "\n\n".join(volume_summaries[i] for i in kept)
            if len(group_text) > budget:
                logger.error(f"复检组（{len(group)}个伏笔，最早埋设第{min_first}章）砍卷后仍超预算"
                             f"（{len(group_text)}>{budget}），跳过该组")
                self._emit_progress({"type": "status",
                                     "message": f"⚠️ 复检：{len(group)}个伏笔因上下文超限跳过"})
                continue
            dropped = len(volume_ranges) - len(kept)
            if dropped:
                logger.info(f"复检上下文超限降级：组内最早埋设第{min_first}章，砍掉埋设前 {dropped} 卷（召回无损）")
                self._emit_progress({"type": "status",
                                     "message": f"复检卷摘要超限，按埋设章砍掉 {dropped} 卷（不影响召回）"})
            plans.append((group, group_text))
        return plans
```

- 替换 `_run_global_foreshadow_recheck` 中 :1235-1258（`full_text = ...` 到子批构建循环结束）为：

```python
        full_text = "\n\n".join(volume_summaries)
        recheck_batch_size = max(1, int(self.config.analysis.foreshadow_recheck_batch_size or 40))
        total_rechecked = 0

        # 复检上下文策略（2026-08-07 用户决策）：默认维持原逻辑——每个子批携带
        # 全量卷摘要，不做任何裁剪（KV cache 厂商只有第一份全文付全价）。
        # 2026-08-17 补充：仅当全量卷摘要超 RECHECK_FULLTEXT_BUDGET_CHARS 时，
        # 由 _plan_recheck_batches 按埋设章砍"埋设前的卷"（召回无损）或整组跳过，
        # 防止超模型上下文走完整重试链烧钱。

        # 准备所有复检子批次
        recheck_batches = []
        for plan_items, plan_text in self._plan_recheck_batches(
                recheck_items, full_text, volume_summaries, volume_ranges):
            for batch_start in range(0, len(plan_items), recheck_batch_size):
                batch_items = plan_items[batch_start:batch_start + recheck_batch_size]
                lines = []
                for i, item in enumerate(batch_items, 1):
                    chapters_str = ",".join(str(c) for c in item.get("evidence_chapters", [])[:5])
                    lines.append(f'{i}. id="{item["id"]}"  描述: {item["clue"]}')
                    lines.append(f"   首次出现: 第{item.get('first_seen', 0)}章  最近出现: 第{item.get('last_seen', 0)}章  证据: [{chapters_str}]")
                recheck_prompt = GLOBAL_RECHECK_USER_TEMPLATE.format(
                    book_name=self.book_name, volume_summaries=plan_text,
                    active_foreshadows_block='\n'.join(lines)
                )
                recheck_batches.append((batch_items, recheck_prompt))

        if not recheck_batches:
            logger.warning("复检计划为空（全部超预算跳过），结束全书复检")
            return
```

注意：原 :1236-1237 的 `recheck_batch_size = 40` 与 `total_rechecked = 0` 两行被上述替换吸收，不要重复保留；原 :1239-1243 的旧决策注释整体替换为新注释。

- 调用点 :1459-1466 改为：

```python
        # 按 batch_idx 排序，恢复顺序
        volume_summaries = []
        volume_ranges: List[Tuple[int, int]] = []
        for idx, ch_start, ch_end, _ in batch_tasks:
            text = volume_results.get(idx, "")
            if text:
                volume_summaries.append(f"=== 第{ch_start}-{ch_end}章 ===\n{text}")
                volume_ranges.append((ch_start, ch_end))

        # 全书伏笔复检
        await self._run_global_foreshadow_recheck(volume_summaries, foreshadow_catalog, volume_ranges)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/ -k plan_recheck -x -q`
Expected: 4 passed

- [ ] **Step 5: 回归全量测试**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/ -x -q`
Expected: 全部通过（62 个既有用例 + 新增）

---

### Task 2: 复检批大小设置项（前端）

**Files:**
- Modify: `frontend/src/api/client.ts`（AppConfigDto.analysis，:118 `summary_batch_size` 后）
- Modify: `frontend/src/pages/SettingsPage.vue`（:424 总结批次大小输入框后）

**Interfaces:**
- Consumes: Task 1 的 `AnalysisConfig.foreshadow_recheck_batch_size`（后端 settings GET/PUT 是泛型读写 dataclass，无需改后端路由）。
- Produces: 无（纯 UI）。

- [ ] **Step 1: client.ts DTO 加字段**

`AppConfigDto.analysis` 中 `summary_batch_size: number`（:118）后加：

```ts
    foreshadow_recheck_batch_size: number
```

- [ ] **Step 2: SettingsPage 加输入框**

在 :424 的"总结批次大小"那行 `</div>` 后插入：

```html
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--win-text-secondary)" title="全书伏笔复检时每次 LLM 调用携带的活跃伏笔数；调大可减少调用次数省钱，单批过大可能稀释注意力降低判断质量">复检批大小:</label><input v-model.number="config.analysis.foreshadow_recheck_batch_size" type="number" min="10" max="200" class="glass-input" style="width: 90px" /></div>
```

- [ ] **Step 3: 构建验证**

Run: `cd frontend; npm run build`（esbuild 网络盘报错则改用 `cmd /c "build_frontend.bat < nul"`，项目根目录）
Expected: 构建成功，无 TS 类型错误

---

### Task 3: Token 统计落盘 + 按书历史端点（后端）

**Files:**
- Modify: `backend/services/queue_service.py`（`_run_one_item`，:668 前加快照、:702-712 finally 加落盘调用、新增 `_persist_book_token_stats` 方法）
- Modify: `backend/services/summary_service.py`（`_run` 的 finally，:158-159）
- Modify: `backend/api/routes_books.py`（:114 后加新端点）
- Test: `backend/tests/` 下找 books 路由/queue 相关测试文件（先 `grep -l "routes_books\|get_book_report\|token_stats" backend/tests`），照其 fixture 追加；无则新建 `backend/tests/test_book_token_stats.py`

**Interfaces:**
- Consumes: `safe_save_json`（`backend/utils/json_utils.py`——**先 `grep "safe_save_json(" backend -n` 确认参数顺序再写调用**）；`book_service.get_output_dir`；`QueueItem.workspace_dir: Path`、`item.start_time/end_time`。
- Produces: 端点 `GET /api/books/{book_id}/token_stats` → `{"book_id": str, "analysis"?: {...}, "summary"?: {...}}`；落盘文件 `output/token_stats.json`（分析）与 `output/summary_token_stats.json`（总结）。Task 4 前端依赖此响应结构。

- [ ] **Step 1: 写失败测试**

```python
def test_get_book_token_stats_reads_both_files(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir(parents=True)
    (output / "token_stats.json").write_text(
        '{"type":"analysis","categories":{"chapter":{"input_tokens":100,"output_tokens":50}}}',
        encoding="utf-8")
    (output / "summary_token_stats.json").write_text(
        '{"type":"summary","categories":{"summary":{"input_tokens":10,"output_tokens":5}}}',
        encoding="utf-8")
    # monkeypatch book_service.get_output_dir 返回 tmp_path，TestClient 调
    # GET /api/books/任意/token_stats，断言 analysis/summary 两键都在且值正确

def test_get_book_token_stats_404_for_unknown_book(monkeypatch):
    # get_output_dir 返回 None → 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/ -k book_token_stats -x -q`
Expected: FAIL（404 路由不存在）

- [ ] **Step 3: 实现**

3a. `backend/services/queue_service.py` `_run_one_item`：在 `self._pipeline = AnalysisPipeline(`（:668）**之前**插入基线快照：

```python
        # token 落盘基线快照（2026-08-17）：本书开始前的累计值，finally 中做差得本书消耗
        stats_baseline = {
            "chapter_stats_len": len(self._chapter_stats),
            "categories": {k: dict(v) for k, v in self._token_stats.items()},
            "cached": self._total_cached_tokens,
            "retries": self._total_retries,
            "failed_tokens": self._total_failed_tokens,
        }
```

在 finally 块中 `self._pipeline = None`（:712）**之后**加：

```python
            self._persist_book_token_stats(item, stats_baseline)
```

新增方法（放在 `_archive_item` 前）：

```python
    def _persist_book_token_stats(self, item: QueueItem, baseline: Dict[str, Any]) -> None:
        """本书分析 token 消耗落盘 output/token_stats.json（重启后可查历史成本）。
        失败仅记日志，不影响主流程。"""
        try:
            from ..utils.json_utils import safe_save_json
            output_dir = item.workspace_dir / "output"
            if not output_dir.exists():
                return
            categories = {}
            for cat, v in self._token_stats.items():
                before = baseline["categories"].get(cat, {"input_tokens": 0, "output_tokens": 0})
                categories[cat] = {
                    "input_tokens": v["input_tokens"] - before["input_tokens"],
                    "output_tokens": v["output_tokens"] - before["output_tokens"],
                }
            payload = {
                "type": "analysis",
                "book_id": item.name,
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "elapsed": (item.end_time or time.time()) - (item.start_time or time.time()),
                "categories": categories,
                "total_retries": self._total_retries - baseline["retries"],
                "total_failed_tokens": self._total_failed_tokens - baseline["failed_tokens"],
                "cached_tokens": self._total_cached_tokens - baseline["cached"],
                "chapter_stats": self._chapter_stats[baseline["chapter_stats_len"]:],
            }
            safe_save_json(payload, output_dir / "token_stats.json")  # 参数顺序以现有调用为准
        except Exception as e:
            logger.warning(f"《{item.name}》token 统计落盘失败: {e}")
```

3b. `backend/services/summary_service.py` `_run` 的 finally（:158-159）扩展为：

```python
        finally:
            self._finished_at = time.time()
            # 总结 token 落盘（2026-08-17）：历史成本可查，重启不丢
            if self._runner is not None:
                try:
                    stats = self._runner.get_token_stats()
                    if stats:
                        from ..utils.json_utils import safe_save_json
                        payload = {
                            "type": "summary",
                            "book_id": self._book_id,
                            "phase": self._phase,
                            "started_at": self._started_at,
                            "finished_at": self._finished_at,
                            "elapsed": self._finished_at - self._started_at if self._started_at else 0,
                            "categories": stats,
                        }
                        await asyncio.to_thread(safe_save_json, payload,
                                                output_dir / "summary_token_stats.json")
                except Exception as e:
                    logger.warning(f"总结 token 统计落盘失败: {e}")
```

3c. `backend/api/routes_books.py` 在 `get_book_ledger`（:99-113）后加：

```python
@router.get("/{book_id}/token_stats")
async def get_book_token_stats(book_id: str) -> dict:
    """读取该书落盘的 token 统计（分析 token_stats.json + 总结 summary_token_stats.json）"""
    output_dir = book_service.get_output_dir(book_id)
    if output_dir is None:
        raise HTTPException(status_code=404, detail=f"书目不存在: {book_id}")
    result: Dict[str, Any] = {"book_id": book_id}
    for key, name in (("analysis", "token_stats.json"), ("summary", "summary_token_stats.json")):
        path = output_dir / name
        if path.exists():
            try:
                result[key] = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"读取 {name} 失败: {e}")
    return result
```

（`Dict`/`Any` 若未导入则在顶部 `from typing import` 补上。）

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/ -x -q`
Expected: 全部通过

---

### Task 4: Token 可见性（前端）

**Files:**
- Modify: `frontend/src/api/client.ts`（收紧 summary status 的 token_stats 类型 + 新增 `getBookTokenStats`）
- Modify: `frontend/src/pages/SummaryPage.vue`（进度区加本次总结 token 行）
- Modify: `frontend/src/pages/StatsPage.vue`（加按书历史查看）

**Interfaces:**
- Consumes: Task 3 的 `GET /api/books/{book_id}/token_stats`；现有 `getSummaryStatus` 响应里已有的 `token_stats` 字段（`Record<string, {input_tokens, output_tokens}>`，key 为 summary/reconciliation/recheck/style/final）。
- Produces: client.ts 方法 `getBookTokenStats(book_id)`。

- [ ] **Step 1: client.ts**

summary status 响应类型中 `token_stats: Record<string, unknown>`（:178）改为：

```ts
  token_stats: Record<string, TokenCategory>
```

在 `getTokenStats`（:255）后加：

```ts
  getBookTokenStats: (book_id: string) => request<{ book_id: string; analysis?: Record<string, unknown>; summary?: Record<string, unknown> }>(`/api/books/${book_id}/token_stats`),
```

- [ ] **Step 2: SummaryPage 加本次总结 token 展示**

先读 SummaryPage.vue 找到 4 阶段进度区与 status 轮询代码。在进度区下方加一行（复用页面现有 ref，假设轮询结果存于 `status`；按实际变量名适配）：

```html
      <div v-if="summaryTokenText" class="text-xs" style="color: var(--win-text-secondary)">{{ summaryTokenText }}</div>
```

script 中加 computed：

```ts
const SUMMARY_TOKEN_LABELS: Record<string, string> = {
  summary: '分卷摘要', reconciliation: '伏笔调和', recheck: '全书复检', style: '风格分析', final: '最终报告',
}
const summaryTokenText = computed(() => {
  const stats = status.value?.token_stats   // 按页面实际 status ref 名适配
  if (!stats) return ''
  const parts = Object.entries(SUMMARY_TOKEN_LABELS)
    .map(([k, label]) => {
      const v = (stats as Record<string, { input_tokens: number; output_tokens: number }>)[k]
      return v && (v.input_tokens || v.output_tokens) ? `${label} 入${v.input_tokens} 出${v.output_tokens}` : ''
    })
    .filter(Boolean)
  return parts.length ? `本次总结 token：${parts.join(' · ')}` : ''
})
```

- [ ] **Step 3: StatsPage 加按书历史**

先读 StatsPage.vue 与 `components/BookSelector.vue`、`components/TokenBadge.vue`。在页面顶部（现有"实时统计"区之上或之下，跟随页面现有布局风格）加：

```html
    <div class="flex gap-2 items-center flex-wrap">
      <div class="flex-1 min-w-[200px]"><BookSelector v-model="historyBookId" /></div>
      <button @click="loadHistory" :disabled="!historyBookId" class="glass-button">查看历史</button>
    </div>
    <div v-if="historyError" class="glass-tinted-red px-4 py-2 rounded text-sm">{{ historyError }}</div>
    <div v-if="historyData" class="glass-card p-4 text-sm space-y-3">
      <div v-if="historyData.analysis">
        <div class="font-medium mb-1">分析阶段（{{ historyData.analysis.finished_at || '时间未知' }}）</div>
        <div v-for="(v, k) in (historyData.analysis.categories || {})" :key="'a'+k">
          {{ k }}：入 {{ v.input_tokens }} / 出 {{ v.output_tokens }}
        </div>
        <div v-if="historyData.analysis.cached_tokens">KV 缓存命中：{{ historyData.analysis.cached_tokens }}</div>
        <div v-if="historyData.analysis.elapsed">耗时：{{ Math.round(historyData.analysis.elapsed) }} 秒</div>
        <div v-if="historyData.analysis.chapter_stats">逐章记录：{{ historyData.analysis.chapter_stats.length }} 条</div>
      </div>
      <div v-if="historyData.summary">
        <div class="font-medium mb-1">总结阶段（{{ historyData.summary.finished_at || '时间未知' }}）</div>
        <div v-for="(v, k) in (historyData.summary.categories || {})" :key="'s'+k">
          {{ k }}：入 {{ v.input_tokens }} / 出 {{ v.output_tokens }}
        </div>
      </div>
      <div v-if="!historyData.analysis && !historyData.summary" style="color: var(--win-text-disabled)">该书暂无历史统计</div>
    </div>
```

script 中加入（`any` 仅用于落盘 JSON 的宽松渲染，TS 严格性靠可选链保证）：

```ts
const historyBookId = ref('')
const historyData = ref<{ analysis?: any; summary?: any } | null>(null)
const historyError = ref('')
async function loadHistory() {
  historyError.value = ''
  historyData.value = null
  try {
    historyData.value = await api.getBookTokenStats(historyBookId.value)
  } catch (e) { historyError.value = (e as Error).message }
}
```

样式只用 `--win-*` 变量与 `glass-*` 类；字段缺失时对应行自动不渲染（v-if 已防）。

- [ ] **Step 4: 构建验证**

Run: `cd frontend; npm run build`（失败则 `cmd /c "build_frontend.bat < nul"`）
Expected: 构建成功，无 TS 错误

---

### Task 5: 桌面端单实例锁 + 关闭确认

**Files:**
- Modify: `desktop.py`

**Interfaces:**
- Consumes: 现有 `_find_free_port`、`_wait_for_server`、`urllib.request`；端点 `GET /api/analysis/status` 与 `GET /api/summary/status`（响应顶层均有 `running: bool`）。
- Produces: 锁文件 `PROJECT_ROOT/app.lock`（JSON：`{"pid": int, "port": int, "started_at": float}`）；`Api.__init__(self, port: int)`。

- [ ] **Step 1: 实现**

`desktop.py` 顶部 import 区补 `import json`。

在 `_find_free_port` 后新增：

```python
LOCK_FILE = PROJECT_ROOT / "app.lock"


def _read_lock() -> dict:
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _instance_alive(lock: dict) -> bool:
    """以锁中端口做健康检查判定活实例（比 PID 存活检测跨平台可靠）"""
    port = lock.get("port")
    if not port:
        return False
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
        return True
    except Exception:
        return False


def _tasks_running(port: int) -> bool:
    """分析或总结任一在运行即返回 True；查询失败（后端已挂）视为未运行，放行关闭"""
    for url in (f"http://127.0.0.1:{port}/api/analysis/status",
                f"http://127.0.0.1:{port}/api/summary/status"):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("running"):
                return True
        except Exception:
            continue
    return False


def _confirm_exit(window) -> bool:
    """弹原生确认框；返回 True=允许退出"""
    return bool(window.create_confirmation_dialog(
        "确认退出",
        "分析或总结任务正在进行中。\n已完成的进度已落盘，下次启动可断点续跑。\n确定要退出吗？"))
```

`main()` 中：

a) `_install_crash_diagnostics()` 之后、`port = _find_free_port()` 之前插入单实例检查：

```python
    # 单实例保护（2026-08-17）：活实例健康检查通过则提示并退出；
    # 锁文件存在但健康检查失败 = 上次崩溃残留，接管之。
    # 已知竞态：两个实例同时启动且都未写锁时会双双通过（概率极低，可接受）。
    if LOCK_FILE.exists():
        old_lock = _read_lock()
        if _instance_alive(old_lock):
            logger.warning("检测到已有实例在运行，本实例退出")
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, "小说智能分析器已在运行中。\n如确认无实例运行，请删除项目根目录的 app.lock 后重试。",
                    "已在运行", 0x40)
            except Exception:
                print("小说智能分析器已在运行中")
            sys.exit(0)
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
```

b) `logger.info("后端已就绪")` 之后写锁：

```python
    try:
        LOCK_FILE.write_text(
            json.dumps({"pid": os.getpid(), "port": port, "started_at": time.time()},
                       ensure_ascii=False),
            encoding="utf-8")
    except OSError as e:
        logger.warning(f"写入单实例锁失败（不影响启动）: {e}")
```

c) `Api` 类改为持有端口，且 `close()` 先确认后 destroy（destroy 不触发 closing 事件，自定义标题栏的关闭按钮必须自查）：

```python
class Api:
    """暴露给前端的 pywebview JS API（window.pywebview.api）"""

    def __init__(self, port: int):
        self._port = port
```

`close()` 替换为：

```python
    def close(self):
        """关闭窗口（标题栏按钮）：任务运行中先弹确认"""
        import webview
        try:
            win = webview.windows[0]
            if _tasks_running(self._port) and not _confirm_exit(win):
                return
            win.destroy()
        except Exception:
            pass
```

`create_window` 调用中 `js_api=Api()` 改为 `js_api=Api(port)`。

d) 注册 OS 关窗拦截（`window.events.closed += _on_closed` 附近）：

```python
    def _on_closing():
        """OS 关窗（Alt+F4/任务栏关闭）拦截：任务运行中需确认，返回 False 否决关闭"""
        try:
            if _tasks_running(port):
                if not _confirm_exit(window):
                    return False
        except Exception as e:
            logger.error(f"关闭确认检查异常，放行关闭: {e}")
        return True
```

注册行加：`window.events.closing += _on_closing`

e) 退出路径（`webview.start()` 之后、`os._exit(0)` 之前）清锁：

```python
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass
```

- [ ] **Step 2: 语法与导入检查**

Run: `.venv\Scripts\python.exe -c "import ast; ast.parse(open('desktop.py', encoding='utf-8').read()); print('syntax ok')"`
Expected: `syntax ok`

- **已知风险**：`events.closing` 的 `return False` 否决关闭在 WebView2（EdgeChromium）后端上的行为未在本项目验证过；若 Step 3 人工验证发现否决无效，OS 关窗路径降级为"只提示不拦截"（保留 `Api.close` 自定义按钮的确认守卫），并在报告中如实记录。

- [ ] **Step 3: 人工验证清单（写入任务报告，不实际启动常驻服务）**

列出供用户人工验证的步骤：① 双击 run_desktop.bat 启动 → 根目录出现 app.lock；② 不关第一个再启动 → 弹"已在运行"退出；③ 分析运行中点标题栏关闭 → 弹确认，取消则留；④ 正常退出后 app.lock 消失。

---

### Task 6: Prompt 预览支持指定章节

**Files:**
- Modify: `frontend/src/api/client.ts`（:341-342）
- Modify: `frontend/src/pages/PromptPreviewPage.vue`

**Interfaces:**
- Consumes: 后端 `POST /api/prompt/preview` 已支持 `chapter: int = 1`（routes_prompt.py:25），响应已含 `chapter`、`chapter_label`。
- Produces: `api.promptPreview(book_id, max_chars, chapter)`。

- [ ] **Step 1: client.ts**

:341-342 替换为：

```ts
  promptPreview: (book_id: string, max_chars: number = 0, chapter: number = 1) =>
    request<{ book_id: string; chapter: number; chapter_label: string; chapter_total_chars: number; chapter_truncated: boolean; system_prompt: string; user_prompt: string; system_len: number; user_len: number; total_len: number; params: Record<string, unknown> }>('/api/prompt/preview', { method: 'POST', body: JSON.stringify({ book_id, max_chars, chapter }) }),
```

- [ ] **Step 2: PromptPreviewPage.vue**

script 加 `const chapter = ref(1)` 与 `const chapterLabel = ref('')`；`preview()` 中调用改为：

```ts
    const res = await api.promptPreview(bookId.value, Number(maxChars.value) || 0, Number(chapter.value) || 1)
```

并在成功分支加 `chapterLabel.value = res.chapter_label`。

模板在"章节原文上限"那个 label 后加：

```html
      <label class="text-sm flex items-center gap-1" style="color: var(--win-text-secondary)">
        章节号
        <input v-model.number="chapter" type="number" min="1" step="1" class="glass-input" style="width: 90px" placeholder="1" />
      </label>
```

USER 标题行改为显示章节：`<div class="text-sm" style="color: var(--win-text-secondary)">USER（第{{ chapter }}章 {{ chapterLabel }} · {{ usrLen }} 字符）</div>`

- [ ] **Step 3: 构建验证**

Run: `cd frontend; npm run build`
Expected: 构建成功，无 TS 错误

---

### Task 7: 工程卫生 + agent.md 同步（最后执行）

**Files:**
- Modify: `.gitignore`
- Modify: `build_frontend.bat`（:28）
- Modify: `run_desktop.bat`（:19-24）
- Modify: `agent.md`（多处，见 Step 4）
- Delete（回收站，不永久删）: 根目录 `desktop.py.bak`、`run_desktop.bat.bak`；`frontend/` 下所有 `.bak_*` 文件与目录、`frontend/scripts/.bak_before_nobackdrop_ensure-backdrop.mjs`、`frontend/.tmp_verify/`（若存在）
- **禁止动 `1/` 目录本身**（只加 gitignore）

**Interfaces:**
- Consumes: Task 1-6 的全部变更事实（配置字段名、新端点、新文件、desktop 行为）。
- Produces: 更新后的 agent.md。

- [ ] **Step 1: .gitignore 追加**

```
# 测试数据与备份残留
1/
.bak_*
app.lock
```

- [ ] **Step 2: build_frontend.bat 排除备份目录**

:28 的 robocopy 行 `/XD node_modules dist .git` 改为 `/XD node_modules dist .git .bak_* .tmp_verify`（robocopy /XD 支持通配目录名）。

- [ ] **Step 3: run_desktop.bat 去硬编码他人路径**

:19-21 替换为（`py` 启动器是 Python 官方 Windows 安装器自带，比写死用户目录可靠）：

```bat
if not defined PYTHON (
  for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
)
```

:24 的错误提示改为：`echo [ERROR] 未找到 python，请安装 Python 3.11+ 并加入 PATH（或安装 py 启动器）。`

- [ ] **Step 4: 回收站删除备份残留（前置安全检查）**

先验证回滚保障：`git status --short` 中 Win11 重做文件应为 `M`（说明旧版在 git HEAD 中，.bak 冗余）。确认后用回收站删除（示例，逐个执行）：

```powershell
pwsh -NoProfile -Command "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('F:\AI\小说分析器\desktop.py.bak','OnlyErrorDialogs','SendToRecycleBin')"
pwsh -NoProfile -Command "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory('F:\AI\小说分析器\frontend\.bak_before_win11','OnlyErrorDialogs','SendToRecycleBin')"
```

（目录用 DeleteDirectory，文件用 DeleteFile；完整清单见 Files 节。任一删除失败跳过并记录，不强行永久删。）

- [ ] **Step 5: agent.md 同步**

实测后更新（测试数先跑 `.venv\Scripts\python.exe -m pytest backend/tests/ --collect-only -q | Select-Object -Last 1` 拿真实数字）：

1. §1 测试统计、"55 个测试用例，17 个测试文件" → 实测数字
2. §2 结构树：删去 `run.py` 行；`desktop.py` 描述补"单实例锁 + 关闭确认"
3. §3 后端表 FastAPI 行"端口 8088" → "桌面端动态端口（desktop.py `_find_free_port`）；开发模式示例 8088"
4. §4.7 最终总结：补一条"复检超限保护：全量卷摘要超 `RECHECK_FULLTEXT_BUDGET_CHARS`（默认 15 万字符）时按伏笔埋设章砍埋设前卷（召回无损），仍超则跳过该组不调用 LLM"
5. §5.2 queue_service：补"每本书分析完成后 token 消耗落盘 `output/token_stats.json`"；summary_service：补"总结结束落盘 `output/summary_token_stats.json`"
6. §5.4 API 表：routes_books 9→10 端点（加 `/{book_id}/token_stats`），总计 55→56
7. §6.4 页面表：StatsPage 职责补"按书历史统计"；PromptPreviewPage 补"可指定章节号"；SettingsPage 补"复检批大小"
8. §6.4 GraphPage 行"SVG 力导向布局" → "SVG 静态环形布局"
9. §7.3 AnalysisConfig 提及处补 `foreshadow_recheck_batch_size`（默认 40）
10. §9.1 gitignore 说明补 `app.lock`、`1/`、`.bak_*`
11. §10 当前状态：追加一条 2026-08-17 变更摘要（本计划五项内容）
12. 顺手检查 README.md 是否写有固定端口；若有，对齐为"开发模式示例端口（桌面端为动态端口）"

- [ ] **Step 6: 最终验证**

Run: `.venv\Scripts\python.exe -m pytest backend/tests/ -x -q`；`cd frontend; npm run build`
Expected: 后端全绿；前端构建成功
