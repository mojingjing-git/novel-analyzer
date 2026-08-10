# 选书标记 + 队列自动总结 + 总结参数后端持久化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 选书下拉标记已完成总结/聚合的书；队列全部分析完成后可自动逐本执行最终总结；总结并发/批次参数改为后端 config.json 持久化。

**Architecture:** 后端 `AnalysisConfig` 新增三个字段（仿 auto_archive 模式）；`queue_service` 收尾处调 `_run_auto_summaries` 逐本复用 `SummaryService.start`；前端 BookSelector 展示已有 `has_report`/`has_aggregated` 字段，SummaryPage 参数读写 config.json。

**Tech Stack:** Python 3.13 / FastAPI / asyncio；Vue3 + TS + Vite

## Global Constraints

- 配置持久化走 `backend/config/settings.py` dataclass + `ConfigManager`（config.json），不得引入新存储
- `PUT /api/settings` 是全量覆盖（`AppConfig.from_dict`），前端写回必须全量 GET→改→PUT
- `summary_service.start()` 有"分析运行时禁止启动"护栏，自动总结仅在分析收尾（非停止）后触发
- 自动总结复用既有 WS（summary_progress）广播，不新增前端进度通道
- 总结并发默认 2（避免高并发触发 API 硬超时，见 crash.log 超时教训）
- 前端改动后必须 `npm run build`（后端托管 frontend/dist）

---

### Task 1: 后端配置新增自动总结字段

**Files:**
- Modify: `backend/config/settings.py:88`（auto_archive 之后）
- Test: `backend/tests/test_config_summary.py`（新建）

**Interfaces:**
- Consumes: `AppConfig.from_dict`（已支持任意 dict→dataclass，新字段缺失用默认值）
- Produces: `AnalysisConfig.auto_summary: bool = False`、`AnalysisConfig.summary_concurrency: int = 2`、`AnalysisConfig.summary_batch_size: int = 30`

- [ ] **Step 1: 写失败测试**

```python
"""
测试自动总结相关配置字段（AnalysisConfig）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config.settings import AnalysisConfig, AppConfig


def test_summary_fields_defaults():
    """默认值：自动总结关闭，并发 2，批次 30"""
    cfg = AnalysisConfig()
    assert cfg.auto_summary is False
    assert cfg.summary_concurrency == 2
    assert cfg.summary_batch_size == 30
    print("✅ test_summary_fields_defaults passed")


def test_summary_fields_from_dict():
    """from_dict 显式值生效；缺失字段用默认"""
    cfg = AppConfig.from_dict({"analysis": {"auto_summary": True, "summary_concurrency": 4}})
    assert cfg.analysis.auto_summary is True
    assert cfg.analysis.summary_concurrency == 4
    assert cfg.analysis.summary_batch_size == 30
    print("✅ test_summary_fields_from_dict passed")


def test_summary_fields_roundtrip():
    """to_dict → from_dict 往返不丢字段"""
    cfg = AppConfig.from_dict({"analysis": {"auto_summary": True, "summary_concurrency": 3, "summary_batch_size": 40}})
    d = cfg.to_dict()
    cfg2 = AppConfig.from_dict(d)
    assert cfg2.analysis.auto_summary is True
    assert cfg2.analysis.summary_concurrency == 3
    assert cfg2.analysis.summary_batch_size == 40
    print("✅ test_summary_fields_roundtrip passed")


if __name__ == "__main__":
    test_summary_fields_defaults()
    test_summary_fields_from_dict()
    test_summary_fields_roundtrip()
    print("\n🎉 All config summary tests passed!")
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONIOENCODING='utf-8'; python backend\tests\test_config_summary.py`
Expected: FAIL（AttributeError: AnalysisConfig 无 auto_summary）

- [ ] **Step 3: 实现**

`backend/config/settings.py` 在 `auto_archive: bool = False` 行后追加：

```python
    auto_archive: bool = False  # 队列分析完成后自动归档整个小说目录到 分析结果/
    # 自动总结：队列全部完成后对分析成功的书逐本执行最终总结
    auto_summary: bool = False  # 队列分析完成后自动开始最终总结
    summary_concurrency: int = 2  # 最终总结并发数（默认 2，避免高并发触发 API 超时）
    summary_batch_size: int = 30  # 最终总结每批章节数
```

- [ ] **Step 4: 运行确认通过**

Run: `$env:PYTHONIOENCODING='utf-8'; python backend\tests\test_config_summary.py`
Expected: PASS（3 个 ✅）

- [ ] **Step 5: 全量回归**

Run: `$env:PYTHONIOENCODING='utf-8'; python -m pytest backend\tests 2>$null | Select-Object -Last 2`
Expected: 全部 passed

---

### Task 2: 选书下拉标记完成状态

**Files:**
- Modify: `frontend/src/api/client.ts:129-134`（BookInfo 接口）
- Modify: `frontend/src/components/BookSelector.vue:30-32`（option 模板）

**Interfaces:**
- Consumes: 后端 `book_info` 已返回 `has_report`/`has_aggregated`（book_service.py:149,162），无需后端改动
- Produces: 下拉 option 文本含 `✓已总结`/`✓已聚合`

- [ ] **Step 1: 类型加字段**

`frontend/src/api/client.ts` 的 `BookInfo` 改为：

```ts
export interface BookInfo {
  id: string
  name: string
  source: 'workspace' | 'archive'
  total_chapters: number
  has_report?: boolean
  has_aggregated?: boolean
}
```

- [ ] **Step 2: option 显示标记**

`frontend/src/components/BookSelector.vue` 的 option 模板改为：

```vue
<option v-for="b in books" :key="b.id" :value="b.id">
  {{ b.name }} ({{ b.total_chapters }} 章)
  <template v-if="b.has_report"> ✓已总结</template>
  <template v-if="b.has_aggregated"> ✓已聚合</template>
</option>
```

- [ ] **Step 3: 类型检查**

Run: `npx vue-tsc --noEmit`
Expected: exit 0

---

### Task 3: 设置页加自动总结开关与参数

**Files:**
- Modify: `frontend/src/pages/SettingsPage.vue`（auto_archive 开关所在区块附近，约 :264-267）

**Interfaces:**
- Consumes: `config.analysis.auto_summary` / `summary_concurrency` / `summary_batch_size`（Task 1 字段，getSettings 已透传）
- Produces: 设置页可编辑三项，PUT 全量保存（复用现有保存按钮逻辑）

- [ ] **Step 1: 加 UI**

`frontend/src/pages/SettingsPage.vue` 中 auto_archive 开关之后追加（样式沿用同区 opt-row 模式，参考 :240 的输入样式）：

```vue
        <div class="opt-row">
          <span class="text-sm" style="color: var(--color-system-gray)">队列完成后自动最终总结</span>
          <label class="switch"><input v-model="config.analysis.auto_summary" type="checkbox" /><span class="slider"></span></label>
        </div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">总结并发数:</label><input v-model.number="config.analysis.summary_concurrency" type="number" min="1" max="20" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">总结批次(章/批):</label><input v-model.number="config.analysis.summary_batch_size" type="number" min="5" class="glass-input" style="width: 90px" /></div>
```

- [ ] **Step 2: 类型检查**

Run: `npx vue-tsc --noEmit`
Expected: exit 0

---

### Task 4: 队列收尾自动总结

**Files:**
- Modify: `backend/services/queue_service.py`（新增 `_run_auto_summaries` 方法；`_run_queue` 收尾 else 分支调用）
- Test: `backend/tests/test_queue_auto_summary.py`（新建）

**Interfaces:**
- Consumes: `config.analysis.auto_summary`、`summary_batch_size`、`summary_concurrency`（Task 1）；`get_summary_service()` 返回的 `SummaryService`（`.start(book_id, start, end, batch_size, concurrency)`、`.is_running`）
- Produces: `QueueService._run_auto_summaries(config: AppConfig) -> None`（async），仅处理 `item.status == "done"` 的书，逐本串行

- [ ] **Step 1: 写失败测试**

```python
"""
测试队列收尾自动总结（_run_auto_summaries）
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.config.settings import AppConfig
from backend.services import queue_service


def _make_service(items):
    svc = object.__new__(queue_service.QueueService)  # 绕过 __init__（避免真实文件 IO）
    svc.queue = MagicMock()
    svc.queue.items = items
    return svc


async def test_auto_summary_runs_for_done_books():
    """只对 status=done 的书自动总结，逐本串行，参数取自 config"""
    svc = _make_service([
        MagicMock(name="书A", status="done"),
        MagicMock(name="书B", status="failed"),
        MagicMock(name="书C", status="done"),
    ])
    fake_summary = MagicMock()
    fake_summary.is_running = False  # 启动后立即完成，不进入轮询
    hub = MagicMock()
    hub.log = AsyncMock()

    config = AppConfig.from_dict({"analysis": {"summary_concurrency": 3, "summary_batch_size": 40}})
    with patch("backend.services.queue_service.get_summary_service", return_value=fake_summary), \
         patch("backend.services.queue_service.get_hub", return_value=hub), \
         patch("backend.services.queue_service.asyncio.sleep", new=AsyncMock()):
        await svc._run_auto_summaries(config)

    assert fake_summary.start.call_count == 2
    fake_summary.start.assert_any_call("书A", 1, 99999, 40, 3)
    fake_summary.start.assert_any_call("书C", 1, 99999, 40, 3)
    print("✅ test_auto_summary_runs_for_done_books passed")


async def test_auto_summary_skips_start_errors():
    """单本启动失败不影响后续"""
    svc = _make_service([
        MagicMock(name="书A", status="done"),
        MagicMock(name="书B", status="done"),
    ])
    fake_summary = MagicMock()
    fake_summary.is_running = False
    fake_summary.start = MagicMock(side_effect=[RuntimeError("已在运行"), None])
    hub = MagicMock()
    hub.log = AsyncMock()

    with patch("backend.services.queue_service.get_summary_service", return_value=fake_summary), \
         patch("backend.services.queue_service.get_hub", return_value=hub), \
         patch("backend.services.queue_service.asyncio.sleep", new=AsyncMock()):
        await svc._run_auto_summaries(AppConfig.from_dict({}))

    assert fake_summary.start.call_count == 2
    print("✅ test_auto_summary_skips_start_errors passed")


async def test_auto_summary_no_done_books():
    """没有完成的书则不触发"""
    svc = _make_service([
        MagicMock(name="书A", status="failed"),
    ])
    fake_summary = MagicMock()
    hub = MagicMock()
    hub.log = AsyncMock()
    with patch("backend.services.queue_service.get_summary_service", return_value=fake_summary), \
         patch("backend.services.queue_service.get_hub", return_value=hub):
        await svc._run_auto_summaries(AppConfig.from_dict({}))
    assert fake_summary.start.call_count == 0
    print("✅ test_auto_summary_no_done_books passed")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_auto_summary_runs_for_done_books())
    asyncio.run(test_auto_summary_skips_start_errors())
    asyncio.run(test_auto_summary_no_done_books())
    print("\n🎉 All auto summary tests passed!")
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONIOENCODING='utf-8'; python backend\tests\test_queue_auto_summary.py`
Expected: FAIL（AttributeError: QueueService 无 _run_auto_summaries）

- [ ] **Step 3: 实现方法**

`backend/services/queue_service.py` 在 `_run_queue` 之后新增：

```python
    async def _run_auto_summaries(self, config: AppConfig) -> None:
        """队列全部完成后：对分析成功的书逐本自动执行最终总结（串行）"""
        from .summary_service import get_summary_service  # 延迟导入避免循环依赖

        hub = get_hub()
        done_books = [item.name for item in self.queue.items if item.status == "done"]
        if not done_books:
            return
        await hub.log(f"队列分析完成，开始自动最终总结 {len(done_books)} 本书...")
        svc = get_summary_service()
        for name in done_books:
            try:
                svc.start(name, 1, 99999,
                          config.analysis.summary_batch_size,
                          config.analysis.summary_concurrency)
            except Exception as e:
                logger.error(f"自动总结启动失败《{name}》: {e}")
                continue
            while svc.is_running:
                await asyncio.sleep(2)
```

- [ ] **Step 4: `_run_queue` 收尾处调用**

`backend/services/queue_service.py` 收尾（约 :503-508）：

```python
            # 收尾
            self.save_queue()
            if self._stop_requested:
                await hub.state_change("stopped", "分析已停止")
            else:
                await hub.state_change("done", "队列全部完成")
                if config.analysis.auto_summary:
                    await self._run_auto_summaries(config)
            self._pipeline = None
```

- [ ] **Step 5: 运行确认通过**

Run: `$env:PYTHONIOENCODING='utf-8'; python backend\tests\test_queue_auto_summary.py`
Expected: PASS（3 个 ✅）

- [ ] **Step 6: 全量回归**

Run: `$env:PYTHONIOENCODING='utf-8'; python -m pytest backend\tests 2>$null | Select-Object -Last 2`
Expected: 全部 passed

---

### Task 5: 总结页参数后端持久化

**Files:**
- Modify: `frontend/src/api/client.ts`（AppConfigDto.analysis 加字段）
- Modify: `frontend/src/pages/SummaryPage.vue:60-63,151`

**Interfaces:**
- Consumes: `getSettings()`（返回含 analysis.summary_*）、`putSettings(config)`（全量覆盖）
- Produces: 页面 batchSize/concurrency 与后端 config.json 同步；移除 localStorage

- [ ] **Step 1: 类型加字段**

`frontend/src/api/client.ts` 的 `analysis` 对象（AppConfigDto）中 `auto_archive: boolean` 行后加：

```ts
    auto_archive: boolean
    auto_summary: boolean
    summary_concurrency: number
    summary_batch_size: number
```

- [ ] **Step 2: SummaryPage 改造**

`frontend/src/pages/SummaryPage.vue`：

- 移除 localStorage 两行 watch（:62-63），`batchSize`/`concurrency` 定义改为：

```ts
const batchSize = ref(30)
const concurrency = ref(2)
let settingsLoaded = false
watch([batchSize, concurrency], async () => {
  if (!settingsLoaded) return
  try {
    const cfg = await api.getSettings()
    cfg.analysis.summary_batch_size = batchSize.value
    cfg.analysis.summary_concurrency = concurrency.value
    await api.putSettings(cfg)
  } catch (e) { console.error('保存总结参数失败:', e) }
})
```

- `onMounted`（:151）改为先加载配置再刷新状态：

```ts
onMounted(async () => {
  try {
    const cfg = await api.getSettings()
    batchSize.value = cfg.analysis.summary_batch_size || 30
    concurrency.value = cfg.analysis.summary_concurrency || 2
  } catch (e) { console.error(e) }
  settingsLoaded = true
  refreshSummaryStatus(); loadAggFiles(); loadReport()
  pollTimer = setInterval(refreshSummaryStatus, 2000)
})
```

- [ ] **Step 3: 类型检查**

Run: `npx vue-tsc --noEmit`
Expected: exit 0

---

### Task 6: 构建与端到端验证

**Files:** 无代码改动

- [ ] **Step 1: 构建前端**

Run: `npm run build`
Expected: built in ~1.5s，dist/ 更新

- [ ] **Step 2: 后端全量测试**

Run: `$env:PYTHONIOENCODING='utf-8'; python -m pytest backend\tests 2>$null | Select-Object -Last 2`
Expected: 全部 passed（原 45 + 新增 3 配置 + 3 自动总结 = 51）

- [ ] **Step 3: 手动验收清单（告知用户）**
1. 重启桌面应用 → 选书下拉出现 `✓已总结`/`✓已聚合`（已总结的书如《诛仙》）
2. 设置页看到"队列完成后自动最终总结"开关与总结并发/批次输入
3. 总结页改并发数 → 重启应用后保留（config.json 持久化）
4. 开启开关后跑完队列 → 日志出现"队列分析完成，开始自动最终总结 N 本书..."，逐本执行，WS 进度可见
