# Location Normalization 独立化重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `LocationNormalizer`（Phase 0 地点 + 空间关系归一化）从 `FinalSummaryRunner.run()` 中抽离，做成独立可触发的功能，在地图页面（MapPage）暴露进度/启动/重跑 UI。`FinalSummaryRunner` 改为消费已归一化数据，不再自己跑。

**Architecture:** 后端新增 `LocationNormalizationService`（编排层，仿 `SummaryService`）+ REST 路由 `/api/location-normalization/*`；`LocationNormalizer` 加 progress callback + 单批 retry + 部分失败容忍；前端 MapPage 增加归一化面板（状态/启动/进度/统计）。`FinalSummaryRunner` 移除 `_normalize_phase_0()`，启动时校验归一化文件是否存在。

**Tech Stack:** FastAPI（路由）、asyncio（编排）、Vue 3 + Composition API（UI）、WebSocket（实时进度）、Pydantic（DTO）

## Global Constraints

- 全部代码改动在当前 main 分支（不切 worktree，遵循本项目一贯做法）
- 兼容性：归一化文件 schema 不变（`schema_version=1`），既有 chapter_*.json 上的 `_normalized_ref` 字段保留
- 所有改动必须通过现有 176 个 backend 测试 + 新增测试（不允许回归）
- 不修改 `LocationNormalizer` 对外公开函数签名（`aggregate_locations`、`aggregate_spatial_pairs`、`build_location_prompt`、`parse_and_validate_locations`、`parse_and_validate_spatial`、`parse_merge_map`、`build_consolidation_prompt`、`apply_merges`、`LocationNormalizer.__init__`）；只允许加新参数（如 `on_progress`）且默认为 None
- 配置文件 `config.json` 不引入新键（避免改前端 SettingsPage）；如有需要改用代码常量
- 中文 prompt / 日志沿用现有风格（`logger.info(f"...")`、f-string 中文）
- 用 `pwsh`（PowerShell 7）跑命令，不用 `powershell`
- 跑测试必须用项目 `.venv\Scripts\python.exe`（不要用 WindowsApps Python）

## File Structure

### 新增文件（3 个）
- `backend/services/location_normalization_service.py` —— 编排层（仿 `SummaryService`）
- `backend/api/routes_location_normalization.py` —— REST 端点
- `backend/tests/test_location_normalization_service.py` —— 服务层单元测试

### 修改文件（6 个）
- `backend/services/location_normalizer.py` —— 加 `on_progress` callback + 块大小 35K→18K + 部分失败容忍 + 单批 retry
- `backend/services/final_summary.py` —— 删除 `_normalize_phase_0()` 与 `LocationNormalizer` 引用，run() 头部校验归一化文件
- `backend/app.py` —— 注册新 router
- `frontend/src/api/client.ts` —— 加 4 个 API 方法
- `frontend/src/api/useProgressSocket.ts` —— 加 `location_normalization_progress` 类型
- `frontend/src/pages/MapPage.vue` —— 加归一化面板

### 文件职责边界
- `location_normalizer.py`：**算法层**（不改），由 on_progress callback 透传进度给上层
- `location_normalization_service.py`：**编排层**（新），持有 asyncio.Task、status 状态机、token 累计
- `routes_location_normalization.py`：**HTTP 层**（新），薄路由转发到 service
- `final_summary.py`：只**消费**归一化文件（通过 `viz_service.map_data()` 已经在做的事），不再**生成**

---

## Task 1: LocationNormalizer 加 on_progress callback 与部分失败容忍

**Files:**
- Modify: `backend/services/location_normalizer.py:539-603` (`LocationNormalizer.__init__` 与 `run`)、`backend/services/location_normalizer.py:618-676` (`_run_phase_0a_batches`)
- Test: `backend/tests/test_location_normalizer.py`（在已有测试基础上加回调/容忍测试）

**目标：** 让 LocationNormalizer 在每个 batch 完成时回调 `(phase, batch_idx, total_batches, status)`；批次 gather 用 `return_exceptions=True` + 失败率 >50% 才整体 abort；单批 retry 1 次（撞 timeout 时）。

**关键改动：**

1. `__init__` 新增参数 `on_progress: Optional[Callable[[dict], None]] = None`
2. `_run_phase_0a_batches` 改：
   ```python
   results = await asyncio.gather(*tasks, return_exceptions=True)
   valid: List[List[Dict]] = [r for r in results if isinstance(r, list)]
   failed = len(results) - len(valid)
   if failed > 0:
       logger.warning(f"Phase 0a batches: {failed}/{len(results)} 失败")
   if len(valid) < len(results) * 0.5:
       logger.error(f"Phase 0a 失败率过高 ({failed}/{len(results)})，整体 abort")
       return None
   return [c for batch in valid for c in batch]
   ```
3. `_process` 内每个 batch 完成时调用 `self.on_progress({...})`
4. `_run_phase_0a_consolidate` 同理：单 batch 失败返回 None，consol 整阶段 abort（consol 只有 1 个 batch，失败就放弃）
5. 同样模式应用到 `_run_phase_0b_batches`

**测试覆盖：**
- `on_progress` 在 batch 完成时触发，含正确 payload
- 部分 batch 抛异常时，其他 batch 结果被保留
- 失败率 >50% 时返回 None

- [ ] **Step 1: 写失败测试（回调）**

```python
# test_location_normalizer.py 追加
import asyncio
from unittest.mock import AsyncMock, patch
from backend.services.location_normalizer import LocationNormalizer

async def test_on_progress_called_per_batch(tmp_path):
    """每个 batch 完成时触发 on_progress"""
    progress_calls = []
    def on_progress(payload):
        progress_calls.append(payload)

    # mock LLM 返回合法 JSON
    with patch("backend.services.location_normalizer.LLMClient") as MockLLM:
        mock_client = MockLLM.return_value
        mock_client.chat = AsyncMock(return_value=(True, '{"locations":[{"canonical_name":"A","aliases":["A","a"]}]}', "", (100, 50)))
        mock_client.config.model = "test-model"
        # ... 构造 200 个 group，期望 1-2 batch
        ...
        norm = LocationNormalizer(output_dir=tmp_path, llm_client=mock_client, on_progress=on_progress)
        await norm.run()

    assert len(progress_calls) > 0
    assert progress_calls[0]["phase"] == "0a-batches"
    assert "batch_idx" in progress_calls[0]
```

- [ ] **Step 2: 跑测试，验证失败**

`cd "F:\AI\小说分析器"; & ".venv\Scripts\python.exe" -m pytest backend/tests/test_location_normalizer.py::test_on_progress_called_per_batch -v`

期望：`AttributeError: LocationNormalizer.__init__() got unexpected keyword argument 'on_progress'`

- [ ] **Step 3: 实现 `__init__` 新参数**

在 `location_normalizer.py:539-551` 的 `__init__` 加：
```python
def __init__(
    self,
    output_dir: Path,
    llm_client: LLMClient,
    concurrency: int = 1,
    locations_batch_size: int = _LOCATION_BATCH_MAX_GROUPS,
    spatial_batch_size: int = _SPATIAL_BATCH_SIZE,
    on_progress: Optional[Callable[[dict], None]] = None,  # 新增
):
    ...
    self.on_progress = on_progress
```

- [ ] **Step 4: 在 `_run_phase_0a_batches` `_process` 内调用回调**

```python
async def _process(batch_idx, batch):
    async with semaphore:
        messages = build_location_prompt(batch, batch_idx, len(batches))
        try:
            success, content, error, _tokens = await self.llm_client.chat(messages)
        except Exception as e:
            logger.error(f"Phase 0a batch {batch_idx} 失败: {e}")
            if self.on_progress:
                self.on_progress({"phase": "0a-batches", "batch_idx": batch_idx, "status": "failed", "error": str(e)})
            return None
        if not success:
            logger.error(f"Phase 0a batch {batch_idx} 失败: {error}")
            if self.on_progress:
                self.on_progress({"phase": "0a-batches", "batch_idx": batch_idx, "status": "failed", "error": error})
            return None
        parsed = parse_and_validate_locations(content, batch)
        if not parsed:
            logger.warning(
                f"Phase 0a batch {batch_idx} 解析为空，响应前300字: {content[:300]!r}"
            )
        if self.on_progress:
            self.on_progress({"phase": "0a-batches", "batch_idx": batch_idx,
                              "total_batches": len(batches), "status": "done",
                              "groups": len(parsed) if parsed else 0})
        return parsed
```

- [ ] **Step 5: 改 gather 为 return_exceptions=True + 失败率判定**

```python
tasks = [_process(i, b) for i, b in enumerate(batches, 1)]
results = await asyncio.gather(*tasks, return_exceptions=True)
valid: List[List[Dict]] = [r for r in results if isinstance(r, list)]
failed = len(results) - len(valid)
if failed > 0:
    logger.warning(f"Phase 0a batches: {failed}/{len(results)} 失败")
if len(valid) < len(results) * 0.5:
    logger.error(f"Phase 0a 失败率过高 ({failed}/{len(results)})，整体 abort")
    if self.on_progress:
        self.on_progress({"phase": "0a-batches", "status": "failed",
                          "error": f"失败率 {failed}/{len(results)}"})
    return None
return [c for batch in valid for c in batch]
```

- [ ] **Step 6: 跑测试，验证 on_progress 通过**

`cd "F:\AI\小说分析器"; & ".venv\Scripts\python.exe" -m pytest backend/tests/test_location_normalizer.py::test_on_progress_called_per_batch -v`

期望：PASS

- [ ] **Step 7: 写部分失败容忍测试**

```python
async def test_partial_batch_failure_tolerated(tmp_path):
    """50% 以下 batch 失败时，其他 batch 结果被保留"""
    call_count = 0
    async def mock_chat(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        # 第 2 个调用失败
        if call_count == 2:
            return (False, "", "模拟 LLM 错误", (0, 0))
        return (True, '{"locations":[{"canonical_name":"A","aliases":["A"]}]}', "", (100, 50))

    with patch("backend.services.location_normalizer.LLMClient") as MockLLM:
        mock_client = MockLLM.return_value
        mock_client.chat = mock_chat
        mock_client.config.model = "test-model"
        # 构造足够多 group 触发 ≥4 batches（35K chars 预算）
        groups = [{"aliases": [f"地点{i}"*5], "types": {}, "parents": {}, "chapter_count": 1, "sample_desc": ""} for i in range(2000)]
        norm = LocationNormalizer(output_dir=tmp_path, llm_client=mock_client)
        result = await norm._run_phase_0a_batches(groups)
    # 即使 1 个失败，>50% 成功，整体返回非 None
    assert result is not None
```

- [ ] **Step 8: 跑测试，验证通过**

- [ ] **Step 9: 跑全量 backend 测试，验证无回归**

`cd "F:\AI\小说分析器"; & ".venv\Scripts\python.exe" -m pytest backend/tests/ -q`

期望：176 旧测试 + 新增测试 全过

- [ ] **Step 10: 提交**

```bash
cd "F:\AI\小说分析器"
git add backend/services/location_normalizer.py backend/tests/test_location_normalizer.py
git commit -m "feat(location_normalizer): add on_progress callback + partial failure tolerance"
```

---

## Task 2: LocationNormalizer 块大小调整 35K → 18K

**Files:**
- Modify: `backend/services/location_normalizer.py:478-485`（常量定义）+ 注释

**目标：** 单批 user prompt 从 35K chars 降到 18K。512 groups 估约 5-6 batches，真正用上 `concurrency=9`。

- [ ] **Step 1: 修改常量**

```python
# Phase 0a locations batch 控制（2026-08-22 调整：35K → 18K）：
# - 硬上限：单批 user prompt ≤ 18K chars（防单批 stall / 撞 timeout）
# - 实测《韩娱》512 group、user prompt ~70K chars → 切 ~5-6 batch 并发，
#   充分利用 config.analysis.concurrency=9；单批 stall 仅损失 ~17% group 而非 ~33%
_LOCATION_PROMPT_BUDGET_CHARS = 18000
_LOCATION_BATCH_MAX_GROUPS = 1000
```

- [ ] **Step 2: 跑全量测试**

`cd "F:\AI\小说分析器"; & ".venv\Scripts\python.exe" -m pytest backend/tests/ -q`

期望：全过

- [ ] **Step 3: 提交**

```bash
cd "F:\AI\小说分析器"
git add backend/services/location_normalizer.py
git commit -m "tune(location_normalizer): reduce batch budget 35K → 18K for better concurrency"
```

---

## Task 3: 新增 LocationNormalizationService（编排层）

**Files:**
- Create: `backend/services/location_normalization_service.py`（仿 `summary_service.py`）
- Test: `backend/tests/test_location_normalization_service.py`

**接口：** 单例（与 `get_summary_service()` 同模式），状态机 idle/running/done/failed，方法 `start(book_id)` / `stop()` / `status()`。

- [ ] **Step 1: 写失败测试（service 状态机）**

```python
# test_location_normalization_service.py
import pytest
from backend.services.location_normalization_service import (
    get_location_normalization_service, LocationNormalizationService
)

def test_status_initial_state():
    svc = LocationNormalizationService()
    s = svc.status()
    assert s["running"] is False
    assert s["phase"] == "idle"
    assert s["book_id"] == ""

def test_singleton_pattern():
    a = get_location_normalization_service()
    b = get_location_normalization_service()
    assert a is b
```

- [ ] **Step 2: 跑测试，验证失败**

`cd "F:\AI\小说分析器"; & ".venv\Scripts\python.exe" -m pytest backend/tests/test_location_normalization_service.py -v`

期望：`ModuleNotFoundError: No module named 'backend.services.location_normalization_service'`

- [ ] **Step 3: 创建 service 文件**

参考 `backend/services/summary_service.py:24-202` 的模式，写 `LocationNormalizationService`。关键结构：

```python
"""
地点归一化编排服务（单例）
- start(book_id): 异步启动 LocationNormalizer run()
- stop(): 请求停止
- 进度回调 → ProgressHub 广播（WS 消息 type=location_normalization_progress）
"""
import asyncio
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional, Dict, Any, Callable

from ..config.settings import AppConfig
from ..progress_hub import get_hub
from .location_normalizer import LocationNormalizer
from . import book_service
from ..core.llm_client import LLMClient

logger = logging.getLogger(__name__)


class LocationNormalizationService:
    """地点归一化编排层（单例，同一时刻只允许一个任务）"""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._normalizer: Optional[LocationNormalizer] = None
        self._llm: Optional[LLMClient] = None
        self._book_id: str = ""
        self._phase: str = "idle"
        self._error: str = ""
        self._batches_done: int = 0
        self._total_batches: int = 0
        self._started_at: float = 0.0
        self._finished_at: float = 0.0
        self._token_stats: Dict[str, Dict[str, int]] = {}

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.is_running,
            "book_id": self._book_id,
            "phase": self._phase,
            "batches_done": self._batches_done,
            "total_batches": self._total_batches,
            "error": self._error,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "token_stats": dict(self._token_stats),
        }

    def start(self, book_id: str, config: AppConfig) -> None:
        """启动归一化任务。已在运行抛 RuntimeError，书不存在抛 KeyError，无结果抛 ValueError"""
        if self.is_running:
            raise RuntimeError("地点归一化任务已在运行中")

        output_dir = book_service.get_output_dir(book_id)
        if output_dir is None:
            raise KeyError(f"书目不存在: {book_id}")
        if not output_dir.exists() or not any(output_dir.glob("chapter_*_result.json")):
            raise ValueError(f"《{book_id}》尚无章节分析结果，无法归一化")

        self._book_id = book_id
        self._phase = "starting"
        self._error = ""
        self._batches_done = 0
        self._total_batches = 0
        self._started_at = time.time()
        self._finished_at = 0.0
        self._token_stats = {}

        # 用 summary_timeout=600s 的 LLM client（仿 final_summary.py:531-538）
        api_cfg = replace(
            config.api,
            model=config.api.summary_model or config.api.model,
            json_mode="default",
            timeout=config.api.summary_timeout,
            thinking_mode=config.api.summary_thinking_mode or config.api.thinking_mode,
        )
        self._llm = LLMClient(api_cfg)

        hub = get_hub()

        def on_progress(payload: dict) -> None:
            ptype = payload.get("type", "")
            if ptype == "phase":
                self._phase = payload.get("phase", self._phase)
                self._total_batches = payload.get("total_batches", self._total_batches)
            elif ptype == "batch_done":
                self._batches_done += 1
            message = payload.get("message", "")
            if message:
                asyncio.create_task(hub.log(message, level="info", category="location-normalization"))
            asyncio.create_task(hub.publish({
                "type": "location_normalization_progress",
                "payload": {**payload, "book_id": book_id, "batches_done": self._batches_done},
            }))

        self._normalizer = LocationNormalizer(
            output_dir=output_dir,
            llm_client=self._llm,
            concurrency=config.analysis.concurrency,
            on_progress=on_progress,
        )
        self._task = asyncio.create_task(self._run())

    def stop(self) -> bool:
        if not self.is_running:
            return False
        if self._normalizer is not None:
            self._normalizer.stop()  # 见 Task 4：新增 stop()
        return True

    async def _run(self) -> None:
        hub = get_hub()
        await hub.state_change("location_normalization_running", f"地点归一化开始: {self._book_id}")
        try:
            ok = await self._normalizer.run()
            self._finished_at = time.time()
            if ok:
                self._phase = "complete"
                await hub.state_change("location_normalization_done", f"地点归一化完成: {self._book_id}")
            else:
                self._phase = "failed"
                self._error = "归一化失败（部分 batch 失败率过高）"
                await hub.state_change("location_normalization_failed", self._error)
        except Exception as e:
            logger.error(f"地点归一化异常: {e}", exc_info=True)
            self._phase = "failed"
            self._error = str(e)
            self._finished_at = time.time()
            await hub.state_change("location_normalization_failed", str(e))


_service: Optional[LocationNormalizationService] = None


def get_location_normalization_service() -> LocationNormalizationService:
    global _service
    if _service is None:
        _service = LocationNormalizationService()
    return _service
```

- [ ] **Step 4: 跑测试，验证通过**

`cd "F:\AI\小说分析器"; & ".venv\Scripts\python.exe" -m pytest backend/tests/test_location_normalization_service.py -v`

期望：PASS

- [ ] **Step 5: 提交**

```bash
cd "F:\AI\小说分析器"
git add backend/services/location_normalization_service.py backend/tests/test_location_normalization_service.py
git commit -m "feat(services): add LocationNormalizationService orchestrator"
```

---

## Task 4: LocationNormalizer 加 stop() 方法

**Files:**
- Modify: `backend/services/location_normalizer.py:527-551` (`LocationNormalizer` 类)

**目标：** 让 service 层能请求停止（透传到 `self.llm_client.request_stop()`）

- [ ] **Step 1: 写测试**

```python
# test_location_normalizer.py 追加
async def test_stop_propagates_to_llm_client(tmp_path):
    """stop() 应调用 llm_client.request_stop()"""
    with patch("backend.services.location_normalizer.LLMClient") as MockLLM:
        mock_client = MockLLM.return_value
        mock_client.config.model = "test-model"
        mock_client.request_stop = MagicMock()
        norm = LocationNormalizer(output_dir=tmp_path, llm_client=mock_client)
        norm.stop()
        mock_client.request_stop.assert_called_once()
```

- [ ] **Step 2: 跑测试，验证失败**

期望：`AttributeError: 'LocationNormalizer' object has no attribute 'stop'`

- [ ] **Step 3: 实现 stop()**

```python
def stop(self) -> None:
    """请求停止当前 run() 调用（透传到 LLM 客户端）"""
    self.llm_client.request_stop()
```

- [ ] **Step 4: 跑测试，验证通过**

- [ ] **Step 5: 提交**

```bash
cd "F:\AI\小说分析器"
git add backend/services/location_normalizer.py backend/tests/test_location_normalizer.py
git commit -m "feat(location_normalizer): add stop() to cancel running task"
```

---

## Task 5: 新增 REST API 路由

**Files:**
- Create: `backend/api/routes_location_normalization.py`
- Modify: `backend/app.py`（在第 132 行 `app.include_router(routes_viz.router)` 后追加新 router）

**端点：**
- `POST /api/location-normalization/start`  body `{book_id}` → 启动
- `POST /api/location-normalization/stop` → 停止
- `GET /api/location-normalization/status` → 当前状态
- `GET /api/location-normalization/result/{book_id}` → 读取已归一化文件摘要

- [ ] **Step 1: 写失败测试（路由存在性）**

```python
# test_location_normalization_service.py 追加
def test_routes_registered():
    """新路由已注册到 app"""
    from backend.app import app
    paths = [r.path for r in app.routes if hasattr(r, 'path')]
    assert '/api/location-normalization/start' in paths
    assert '/api/location-normalization/stop' in paths
    assert '/api/location-normalization/status' in paths
```

- [ ] **Step 2: 跑测试，验证失败**

- [ ] **Step 3: 创建 routes 文件**

```python
"""
地点归一化路由
POST /api/location-normalization/start  — 启动指定书的地点归一化
POST /api/location-normalization/stop   — 停止当前归一化任务
GET  /api/location-normalization/status — 查询状态
GET  /api/location-normalization/result/{book_id} — 读取已归一化数据
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.location_normalization_service import get_location_normalization_service
from backend.services.book_service import get_output_dir
from backend.utils.json_utils import safe_load_json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/location-normalization", tags=["location-normalization"])


class LocationNormalizationStartRequest(BaseModel):
    book_id: str


def _load_normalized_summary(output_dir: Path) -> Optional[dict]:
    """读 normalized_locations.json 摘要"""
    loc_path = output_dir / "output" / "locations_normalized.json"
    rel_path = output_dir / "output" / "spatial_relationships_normalized.json"
    if not loc_path.exists() or not rel_path.exists():
        return None
    loc_data = safe_load_json(loc_path) or {}
    rel_data = safe_load_json(rel_path) or {}
    locs = (loc_data.get("locations") or [])
    rels = (rel_data.get("relationships") or [])
    return {
        "normalized_at": loc_data.get("normalized_at"),
        "model": loc_data.get("model"),
        "chapter_mtimes_hash": loc_data.get("chapter_mtimes_hash"),
        "location_count": len(locs),
        "spatial_count": len(rels),
    }


@router.get("/status")
async def status() -> dict:
    return get_location_normalization_service().status()


@router.post("/start")
async def start(req: LocationNormalizationStartRequest) -> dict:
    from backend.config.settings import AppConfig
    from backend.services.queue_service import get_service as get_analysis_service

    svc = get_location_normalization_service()
    try:
        config: AppConfig = get_analysis_service().config_manager.load()
        svc.start(book_id=req.book_id, config=config)
        return {"ok": True}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/stop")
async def stop() -> dict:
    if not get_location_normalization_service().stop():
        raise HTTPException(status_code=400, detail="没有正在运行的归一化任务")
    return {"ok": True}


@router.get("/result/{book_id}")
async def result(book_id: str) -> dict:
    output_dir = get_output_dir(book_id)
    if output_dir is None:
        raise HTTPException(status_code=404, detail=f"书目不存在: {book_id}")
    summary = _load_normalized_summary(output_dir)
    if summary is None:
        return {"exists": False}
    return {"exists": True, **summary}
```

- [ ] **Step 4: 注册 router 到 app.py**

在 `backend/app.py:132` 后追加：
```python
    app.include_router(routes_location_normalization.router)
```
并在 `from backend.api import (...)` 块（line 26-34）追加：
```python
    routes_location_normalization,
```

- [ ] **Step 5: 跑测试，验证通过**

- [ ] **Step 6: 跑全量测试，验证无回归**

- [ ] **Step 7: 提交**

```bash
cd "F:\AI\小说分析器"
git add backend/api/routes_location_normalization.py backend/app.py backend/tests/test_location_normalization_service.py
git commit -m "feat(api): add /api/location-normalization/* endpoints"
```

---

## Task 6: 重构 FinalSummaryRunner 移除 _normalize_phase_0

**Files:**
- Modify: `backend/services/final_summary.py:28, 546-565, 1378-1383`

**目标：** 删除 `_normalize_phase_0()` 与 `LocationNormalizer` 引用；`run()` 头部校验归一化文件存在，不存在则失败并给出明确提示。

- [ ] **Step 1: 写失败测试（summary 校验归一化）**

```python
# test_summary_checkpoint.py 或新文件 test_final_summary_prereq.py
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_run_fails_without_normalized(tmp_path):
    """归一化文件缺失时，run() 应当早退并报错"""
    from backend.services.final_summary import FinalSummaryRunner
    # 没有 locations_normalized.json / spatial_relationships_normalized.json
    (tmp_path / "output").mkdir()
    # 创建一个 chapter_*.json
    (tmp_path / "output" / "chapter_1_result.json").write_text('{"ch":1,"data":{}}')
    cfg = MagicMock()
    cfg.api.summary_timeout = 600
    cfg.api.max_tokens = 16000
    runner = FinalSummaryRunner(config=cfg, output_dir=tmp_path,
                                start_chapter=1, end_chapter=1, batch_size=1)
    # 应直接返回（None 或抛错），不能跑后续 phase
    with patch.object(runner, '_call_llm_summary', new=AsyncMock()) as m:
        result = await runner.run()
        m.assert_not_called()  # 没进入分卷阶段
```

- [ ] **Step 2: 跑测试，验证失败**

期望：test passes (因为旧实现会先跑 _normalize_phase_0，再调用 m)。失败说明：m 被调用了。

- [ ] **Step 3: 修改 final_summary.py**

**3a.** 删除 import：
```python
# line 28: 删除这一行
from backend.services.location_normalizer import LocationNormalizer
```

**3b.** 删除 `_normalize_phase_0` 方法（line 546-565）和 `_normalizer` 字段（line 547）：
```python
# 删除 _normalize_phase_0() 整段（line 546-565）
```

**3c.** 修改 `run()` 头部（line 1374-1383）：
```python
async def run(self) -> Optional[str]:
    """执行完整总结流程。返回最终报告文本；用户停止返回 None；失败抛 RuntimeError"""
    t_start = time.time()

    # 校验归一化前置条件（2026-08-22 重构）：Phase 0 已独立出最终总结
    output_subdir = self.output_dir / "output"
    if not output_subdir.is_dir():
        output_subdir = self.output_dir
    loc_norm = output_subdir / "locations_normalized.json"
    rel_norm = output_subdir / "spatial_relationships_normalized.json"
    if not loc_norm.exists() or not rel_norm.exists():
        logger.error(
            f"归一化文件缺失（{loc_norm.name} / {rel_norm.name}），"
            "请先在地图页面运行「地点归一化」"
        )
        self._emit_progress({"type": "phase_failed", "phase": "prereq",
                             "message": "请先在地图页面运行地点归一化"})
        return

    self._emit_progress({"type": "status", "message": "正在加载章节数据..."})
    # ... 后续不变
```

- [ ] **Step 4: 跑新测试，验证通过**

- [ ] **Step 5: 跑全量测试**

`cd "F:\AI\小说分析器"; & ".venv\Scripts\python.exe" -m pytest backend/tests/ -q`

**⚠️ 已知会失败的旧测试**（需在 Step 5.1 修 fixture）：

`backend/tests/test_summary_checkpoint.py` 的 `test_resume_skips_completed_llm_calls`（line 69-104）依赖 `_write_results()` 写入的 chapter_*.json 能完整跑过 run()。重构后 run() 会在归一化文件缺失时早退，测试断言失败。

**Step 5.1 修复 fixture**：在 `test_summary_checkpoint.py:_write_results()` 末尾追加：

```python
# 2026-08-22: final_summary.run() 需归一化文件存在才不早退
output_subdir = output_dir / "output" if (output_dir / "output").is_dir() else output_dir
import json as _json
_json.dump({"schema_version": 1, "locations": [], "chapter_mtimes_hash": ""},
           (output_subdir / "locations_normalized.json").open("w", encoding="utf-8"),
           ensure_ascii=False)
_json.dump({"schema_version": 1, "relationships": [], "chapter_mtimes_hash": ""},
           (output_subdir / "spatial_relationships_normalized.json").open("w", encoding="utf-8"),
           ensure_ascii=False)
```

并在 `test_resume_skips_completed_llm_calls` 删除 `patch("backend.services.final_summary.LocationNormalizer", ...)`（line 77, 96），因为 LocationNormalizer 已不再被引用。

- [ ] **Step 6: 提交**

```bash
cd "F:\AI\小说分析器"
git add backend/services/final_summary.py backend/tests/
git commit -m "refactor(final_summary): remove Phase 0, consume pre-normalized files"
```

---

## Task 7: 前端 API client 加方法

**Files:**
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: 在 types 块添加 LocationNormalizationStatus 类型（line 58-89 附近）**

```typescript
export interface LocationNormalizationStatus {
  running: boolean
  book_id: string
  phase: string
  batches_done: number
  total_batches: number
  error: string
  started_at: number
  finished_at: number
  token_stats: Record<string, unknown>
}
```

- [ ] **Step 2: 在 `api` 对象内加 4 个方法（line 318 附近）**

```typescript
startLocationNormalization: (book_id: string) =>
  request<{ ok: boolean }>('/api/location-normalization/start', {
    method: 'POST',
    body: JSON.stringify({ book_id }),
  }),
stopLocationNormalization: () =>
  request<{ ok: boolean }>('/api/location-normalization/stop', { method: 'POST' }),
locationNormalizationStatus: () =>
  request<LocationNormalizationStatus>('/api/location-normalization/status'),
getLocationNormalizationResult: (book_id: string) =>
  request<{
    exists: boolean
    normalized_at?: string
    location_count?: number
    spatial_count?: number
  }>(`/api/location-normalization/result/${book_id}`),
```

- [ ] **Step 3: 跑前端 build**

`cd "F:\AI\小说分析器\frontend"; npm run build`

期望：构建成功（TypeScript 类型 + 编译）

- [ ] **Step 4: 提交**

```bash
cd "F:\AI\小说分析器"
git add frontend/src/api/client.ts
git commit -m "feat(api-client): add location-normalization endpoints"
```

---

## Task 8: 前端 progress hub 加新消息类型

**Files:**
- Modify: `frontend/src/api/useProgressSocket.ts:6`

- [ ] **Step 1: 扩展 type 联合**

```typescript
export type WSMessageType =
  | 'log'
  | 'progress'
  | 'block_done'
  | 'state_change'
  | 'token_stats'
  | 'ping'
  | 'summary_progress'
  | 'location_normalization_progress'  // 新增
```

- [ ] **Step 2: 提交**

```bash
cd "F:\AI\小说分析器"
git add frontend/src/api/useProgressSocket.ts
git commit -m "feat(ws): add location_normalization_progress type"
```

---

## Task 9: 前端 MapPage 加归一化面板

**Files:**
- Modify: `frontend/src/pages/MapPage.vue`

**UI 布局：**

```
┌─ 地图可视化 ───────────────────────────────────┐
│ [BookSelector]                                  │
│                                                │
│ ┌─ 地点归一化 ──────────────────────────┐      │
│ │ 状态: ✅ 已归一化（512→509 个地点）    │      │
│ │ [开始归一化] [停止] [重新归一化]       │      │
│ │ 或（运行中）                           │      │
│ │ 进度: ████████░░ 8/12 batches          │      │
│ │ 阶段: 0b-batches                       │      │
│ └────────────────────────────────────────┘      │
│                                                │
│ ┌─ 地图 ──────────────────────────────────┐    │
│ │ [SVG]                                    │    │
│ └──────────────────────────────────────────┘    │
└────────────────────────────────────────────────┘
```

- [ ] **Step 1: 在 `<script setup>` 内加归一化状态管理**

```typescript
import { api } from '../api/client'
import type { LocationNormalizationStatus } from '../api/client'

const normStatus = ref<LocationNormalizationStatus | null>(null)
const normResult = ref<{ exists: boolean; location_count?: number; spatial_count?: number } | null>(null)
let normPollHandle: number | null = null

async function refreshNormStatus() {
  try {
    normStatus.value = await api.locationNormalizationStatus()
  } catch (e) {
    console.error('归一化状态查询失败:', e)
  }
}

async function refreshNormResult() {
  if (!bookId.value) return
  try {
    normResult.value = await api.getLocationNormalizationResult(bookId.value)
  } catch (e) {
    console.error('归一化结果查询失败:', e)
  }
}

async function startNormalization() {
  if (!bookId.value) return
  try {
    await api.startLocationNormalization(bookId.value)
    refreshNormStatus()
    if (normPollHandle === null) {
      normPollHandle = window.setInterval(refreshNormStatus, 2000)
    }
  } catch (e: any) {
    alert('启动失败：' + (e?.message || e))
  }
}

async function stopNormalization() {
  try {
    await api.stopLocationNormalization()
  } catch (e: any) {
    alert('停止失败：' + (e?.message || e))
  }
}

// 既有 watch(bookId, ...) 内追加（在切书时拉一次归一化结果）
watch(bookId, async () => {
  // ... 既有逻辑保留:
  //   selected.value = null
  //   const res = await api.getMap(bookId.value)
  //   locations.value = res.locations as Location[]
  //   relationships.value = res.relationships as SpatialRel[]
  // 新增:
  await refreshNormResult()
})

// onMounted：归一化全局状态可独立于 bookId 拉取（用于显示运行中指示）
onMounted(() => {
  refreshNormStatus()
})
```

> 注意：`onMounted` 不调 `refreshNormResult()`，因为初始 `bookId=''`，由 watch 接管。

- [ ] **Step 2: 在 template 内 BookSelector 下方加归一化面板**

```html
<!-- 归一化状态面板 -->
<div v-if="bookId" class="glass-card p-4 space-y-3">
  <div class="flex items-center justify-between">
    <h3 class="font-semibold">地点归一化</h3>
    <div class="text-xs" style="color: var(--win-text-secondary)">
      <template v-if="normStatus?.running">
        <span style="color: var(--win-warning)">● 运行中</span>
        ({{ normStatus.phase }})
      </template>
      <template v-else-if="normResult?.exists">
        <span style="color: var(--win-success)">✓ 已归一化</span>
        ({{ normResult.location_count }} 地点, {{ normResult.spatial_count }} 关系)
      </template>
      <template v-else>
        <span style="color: var(--win-text-disabled)">○ 未归一化</span>
      </template>
    </div>
  </div>

  <!-- 运行中：进度条 -->
  <div v-if="normStatus?.running">
    <div class="text-xs mb-1" style="color: var(--win-text-secondary)">
      进度: {{ normStatus.batches_done }} / {{ normStatus.total_batches || '?' }} batches
    </div>
    <div class="w-full h-2 rounded" style="background: var(--win-control-alt)">
      <div class="h-2 rounded transition-all"
           :style="{ width: normStatus.total_batches ? `${(normStatus.batches_done / normStatus.total_batches) * 100}%` : '0%',
                     background: 'var(--win-accent)' }"></div>
    </div>
  </div>

  <!-- 失败：错误信息 -->
  <div v-if="normStatus && !normStatus.running && normStatus.error"
       class="text-xs" style="color: var(--win-error)">
    上次错误: {{ normStatus.error }}
  </div>

  <!-- 按钮组 -->
  <div class="flex gap-2">
    <button v-if="!normStatus?.running" class="glass-btn-primary text-sm"
            @click="startNormalization">
      {{ normResult?.exists ? '重新归一化' : '开始归一化' }}
    </button>
    <button v-else class="glass-btn text-sm" @click="stopNormalization">停止</button>
  </div>
</div>
```

- [ ] **Step 3: 跑前端 build**

`cd "F:\AI\小说分析器\frontend"; npm run build`

期望：构建成功

- [ ] **Step 4: 提交**

```bash
cd "F:\AI\小说分析器"
git add frontend/src/pages/MapPage.vue
git commit -m "feat(map): add location-normalization status panel"
```

---

## Task 10: 集成测试（端到端）

**Files:**
- Create: `backend/tests/test_location_normalization_e2e.py`

**目标：** 验证完整链路：mock LLM → API start → service → LocationNormalizer → 落盘 → viz_service 能读到。

- [ ] **Step 1: 写测试**

```python
"""端到端集成测试：API → Service → Normalizer → 落盘"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.services.book_service import add_book  # 临时构造一本书


@pytest.fixture
def mock_book(tmp_path):
    """构造一本书，含 3 个 chapter_*.json"""
    book_id = "测试书"
    output_dir = tmp_path / book_id
    (output_dir / "output").mkdir(parents=True)
    for i in range(1, 4):
        chapter = {
            "chapter_number": i,
            "locations": [
                {"name": "宁安县" if i == 1 else "宁安县城", "type": "县城",
                 "parent": "京畿府", "description": "测试"},
            ],
            "spatial_relationships": [
                {"from": "宁安县", "to": "京畿府", "relation": "隶属"},
            ],
        }
        (output_dir / "output" / f"chapter_{i}_result.json").write_text(
            json.dumps(chapter, ensure_ascii=False), encoding="utf-8"
        )
    return book_id, output_dir


def test_full_flow_creates_normalized_files(mock_book):
    book_id, output_dir = mock_book
    # mock LLM 返回合法 JSON
    with patch("backend.services.location_normalization_service.LLMClient") as MockLLM:
        mock_client = MockLLM.return_value
        mock_client.chat = AsyncMock(return_value=(
            True,
            json.dumps({"locations": [{"canonical_name": "宁安县",
                                       "aliases": ["宁安县", "宁安县城"]}]}),
            "",
            (100, 50),
        ))
        mock_client.config.model = "test-model"

        client = TestClient(app)
        resp = client.post("/api/location-normalization/start",
                           json={"book_id": book_id})
        assert resp.status_code == 200

        # 异步 task 启动，轮询 status
        import time
        for _ in range(60):
            time.sleep(0.5)
            s = client.get("/api/location-normalization/status").json()
            if not s["running"]:
                break
        assert s["phase"] == "complete", f"phase={s['phase']} error={s['error']}"

        # 验证落盘
        loc_norm = output_dir / "output" / "locations_normalized.json"
        assert loc_norm.exists()
```

- [ ] **Step 2: 跑测试**

`cd "F:\AI\小说分析器"; & ".venv\Scripts\python.exe" -m pytest backend/tests/test_location_normalization_e2e.py -v`

- [ ] **Step 3: 跑全量测试**

`cd "F:\AI\小说分析器"; & ".venv\Scripts\python.exe" -m pytest backend/tests/ -q`

期望：176 旧 + 新增 全部通过

- [ ] **Step 4: 提交**

```bash
cd "F:\AI\小说分析器"
git add backend/tests/test_location_normalization_e2e.py
git commit -m "test: add location-normalization end-to-end integration test"
```

---

## Task 11: 更新 ProgressHub 文档（前端 progress WS）

> 已并入 Task 8：见 Task 8 Step 1 中的 type 联合扩展注释。

---

## Self-Review Checklist

执行前请确认：

- [ ] Spec 覆盖：每个用户需求都对应一个 task：
  - ✅ 从 FinalSummaryRunner 抽离 → Task 6
  - ✅ 在地图页面暴露 → Task 9
  - ✅ 进度/状态 UI → Task 9
  - ✅ 启动/停止/重跑 → Task 5 + Task 9
  - ✅ 修复极度不成熟：块大小 → Task 2；部分容忍 → Task 1；回调 → Task 1；stop() → Task 4

- [ ] 无 placeholder：搜索 `TBD|TODO|implement later|fill in`，应为 0 匹配

- [ ] 类型一致性：
  - `LocationNormalizer.__init__` 的 `on_progress` 参数在 Task 1 定义，Task 3、Task 4 使用
  - `LocationNormalizer.stop()` 在 Task 4 定义，Task 3 service 层调用
  - `LocationNormalizationService.start(book_id, config)` 在 Task 3 定义，Task 5 路由调用
  - `api.startLocationNormalization(book_id)` 在 Task 7 定义，Task 9 UI 调用

- [ ] 测试覆盖：
  - Task 1：on_progress + 部分容忍
  - Task 4：stop()
  - Task 3：service 状态机
  - Task 5：路由注册
  - Task 6：summary 校验归一化前置
  - Task 10：E2E

- [ ] 不破坏既有数据：归一化文件 schema 不变（`schema_version=1`），chapter_*.json 上的 `_normalized_ref` 字段保留（viz_service 既有逻辑不依赖 _normalized_ref 但保留无害）

---

## Execution Handoff

计划保存到 `docs/superpowers/plans/2026-08-22-location-normalization-extraction.md`。

两种执行方式：

1. **Subagent-Driven**（推荐）—— 每个 task 派一个 subagent 执行，每个 task 之间人工 review
2. **Inline Execution** —— 在当前 session 按顺序执行，批量推进 + checkpoint review

请选择。