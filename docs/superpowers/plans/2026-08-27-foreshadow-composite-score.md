# 伏笔组合因子排序 + 排行视图

> **计划日期**: 2026-08-27
> **目标**: 修复伏笔 catalog 截断对早期隐蔽伏笔的结构性歧视；前端加排行视图
> **背景**: 现有 sort_key = imp×100 + conf×10 + evidence_count。evidence_count 是"LLM 检测次数"而非"伏笔重要性"，导致好伏笔（隐蔽、早期埋设、后续不被反复检测）反而因 evidence_count 低被截断丢弃。4/13 本书触发高桶截断（>500），约 1447 条"高"伏笔被直接丢弃。

---

## 改动清单

### 1. 后端：组合因子排序 + composite_score 保留

**文件**: `backend/services/final_summary.py`

**改动 1a**: `_build_foreshadow_catalog` 方法内 sort_key 计算（约 L864-875）

当前代码：
```python
# 综合排序：importance * 100 + confidence * 10 + evidence_count
imp_order = {"高": 3, "中": 2, "低": 1}
conf_order = {"高": 3, "中": 2, "低": 1}
for c in catalog:
    c["_sort_key"] = (
        imp_order.get(c.get("importance", "中"), 0) * 100
        + conf_order.get(c.get("confidence", "中"), 0) * 10
        + min(len(c.get("evidence_chapters", [])), 99)
    )
catalog.sort(key=lambda c: c["_sort_key"], reverse=True)
for c in catalog:
    c.pop("_sort_key", None)
```

改为：
```python
# 综合排序：importance * 100 + confidence * 10 + evidence_count(≤50) + span(≤49)
# 组合因子：evidence_count 捕捉"被反复检测"，span 捕捉"跨章时间范围"
# 两者各贡献一半权重，避免单一因子的结构性歧视
imp_order = {"高": 3, "中": 2, "低": 1}
conf_order = {"高": 3, "中": 2, "低": 1}
for c in catalog:
    evidence_count = len(c.get("evidence_chapters", []))
    span = c.get("last_seen", 0) - c.get("first_seen", 0)
    score = (
        imp_order.get(c.get("importance", "中"), 0) * 100
        + conf_order.get(c.get("confidence", "中"), 0) * 10
        + min(evidence_count, 50)
        + min(span, 49)
    )
    c["_sort_key"] = score
    c["composite_score"] = score  # 保留到 catalog 条目，供前端展示
catalog.sort(key=lambda c: c["_sort_key"], reverse=True)
for c in catalog:
    c.pop("_sort_key", None)  # 临时排序键仍删除；composite_score 保留
```

**改动 1b**: ledger 同步（约 L1519-1531）

当前代码：
```python
for cat in foreshadow_catalog:
    if cat["id"] not in existing_ids:
        self.ledger.items.append(ForeshadowItem(
            id=cat["id"], description=cat["clue"],
            first_seen_chapter=cat["first_seen"], first_seen_batch=0,
            last_seen_chapter=cat["last_seen"], last_seen_batch=0,
            evidence_chapters=cat["evidence_chapters"],
            confidence=0.8, source_type='catalog_scan'
        ))
```

改为（加 `composite_score` 和 `importance`）：
```python
for cat in foreshadow_catalog:
    if cat["id"] not in existing_ids:
        self.ledger.items.append(ForeshadowItem(
            id=cat["id"], description=cat["clue"],
            first_seen_chapter=cat["first_seen"], first_seen_batch=0,
            last_seen_chapter=cat["last_seen"], last_seen_batch=0,
            evidence_chapters=cat["evidence_chapters"],
            confidence=0.8, source_type='catalog_scan',
            composite_score=cat.get("composite_score", 0),
            importance=cat.get("importance", "中"),
        ))
```

### 2. 后端：ForeshadowItem dataclass 扩展

**文件**: `backend/utils/foreshadow_ledger.py`

**改动 2a**: `ForeshadowItem` 加两个字段（必须给默认值，老 ledger JSON 兼容）

在 `needs_review` 字段后面加：
```python
    composite_score: int = 0               # 组合因子分数（imp×100 + conf×10 + evi≤50 + span≤49）
    importance: str = "中"                 # 重要度（高/中/低，从 catalog 同步）
```

`from_dict` 白名单构造已用 `fields(cls)` 动态获取，新字段自动包含，无需改 `from_dict`。

**改动 2b**: SCHEMA_VERSION 升级

```python
SCHEMA_VERSION = 3  # v3: ForeshadowItem 新增 composite_score + importance
```

### 3. 后端：排行 API 端点

**文件**: `backend/api/routes_books.py`

在 `get_book_ledger` 端点后面加：

```python
@router.get("/{book_id}/foreshadow_ranking")
async def get_foreshadow_ranking(book_id: str) -> dict:
    """伏笔排行：按组合因子分数从大到小排列"""
    output_dir = book_service.get_output_dir(book_id)
    if output_dir is None:
        raise HTTPException(status_code=404, detail=f"书目不存在: {book_id}")

    ledger_path = output_dir / "foreshadow_ledger.json"
    if not ledger_path.exists():
        raise HTTPException(status_code=404, detail="伏笔账本不存在，请先运行最终总结")

    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        items = data.get("items", [])

        # 计算排行数据
        ranking = []
        for item in items:
            evidence_count = len(item.get("evidence_chapters", []))
            first_seen = item.get("first_seen_chapter", 0)
            last_seen = item.get("last_seen_chapter", 0)
            span = last_seen - first_seen
            score = item.get("composite_score", 0)

            # 旧数据（schema v2）没有 composite_score，实时计算
            if score == 0 and item.get("importance"):
                imp_order = {"高": 3, "中": 2, "低": 1}
                conf_val = item.get("confidence", 0.8)
                # float confidence → string
                if conf_val >= 0.8:
                    conf_str = "高"
                elif conf_val >= 0.5:
                    conf_str = "中"
                else:
                    conf_str = "低"
                score = (
                    imp_order.get(item.get("importance", "中"), 0) * 100
                    + imp_order.get(conf_str, 0) * 10
                    + min(evidence_count, 50)
                    + min(span, 49)
                )

            ranking.append({
                "id": item.get("id", ""),
                "description": item.get("description", ""),
                "composite_score": score,
                "importance": item.get("importance", "中"),
                "confidence": item.get("confidence", 0.8),
                "status": item.get("status", "active"),
                "first_seen_chapter": first_seen,
                "last_seen_chapter": last_seen,
                "span": span,
                "evidence_count": evidence_count,
                "evidence_chapters": item.get("evidence_chapters", []),
                "resolution": item.get("resolution"),
                "resolved_chapter": item.get("resolved_chapter"),
            })

        # 按分数降序
        ranking.sort(key=lambda x: x["composite_score"], reverse=True)

        return {
            "book_id": book_id,
            "total": len(ranking),
            "ranking": ranking,
        }
    except Exception as e:
        logger.error(f"读取伏笔排行失败: {e}")
        raise HTTPException(status_code=500, detail="排行读取失败")
```

### 4. 前端：API client 方法

**文件**: `frontend/src/api/client.ts`

在 `getTimeline` 方法后面加：

```typescript
  getForeshadowRanking: (book_id: string) => request<{
    book_id: string
    total: number
    ranking: ForeshadowRankItem[]
  }>(`/api/books/${seg(book_id)}/foreshadow_ranking`),
```

在 DTO 类型定义区（文件头部 interface 区域）加：

```typescript
interface ForeshadowRankItem {
  id: string
  description: string
  composite_score: number
  importance: string
  confidence: number
  status: string
  first_seen_chapter: number
  last_seen_chapter: number
  span: number
  evidence_count: number
  evidence_chapters: number[]
  resolution: string | null
  resolved_chapter: number | null
}
```

### 5. 前端：TimelinePage 排行视图

**文件**: `frontend/src/pages/TimelinePage.vue`

**改动 5a**: mode 类型扩展

```typescript
// 当前
const mode = ref<'events' | 'foreshadows'>('events')
// 改为
const mode = ref<'events' | 'foreshadows' | 'ranking'>('events')
```

**改动 5b**: 排行数据加载

加 ref + watch（复用已有的 bookId watch，或加独立逻辑）：

```typescript
const ranking = ref<ForeshadowRankItem[]>([])

// 在已有的 bookId watch 里加：
watch(bookId, async () => {
  const seq = ++timelineSeq
  if (!bookId.value) { events.value = []; foreshadows.value = []; ranking.value = []; return }
  try {
    const res = await api.getTimeline(bookId.value)
    if (seq !== timelineSeq) return
    events.value = res.events as TimelineEvent[]
    foreshadows.value = res.foreshadows as Foreshadow[]
    // 排行数据（可能 404，静默处理）
    try {
      const rankRes = await api.getForeshadowRanking(bookId.value)
      if (seq !== timelineSeq) return
      ranking.value = rankRes.ranking
    } catch {
      if (seq !== timelineSeq) return
      ranking.value = []  // 账本不存在，排行为空
    }
  } catch (e) {
    if (seq !== timelineSeq) return
    events.value = []
    foreshadows.value = []
    ranking.value = []
    console.error('加载时间线失败，已清空:', e)
  }
})
```

**改动 5c**: 模板加第三个按钮 + 排行视图

在已有的 mode 按钮区加：
```html
<button @click="mode = 'ranking'" class="glass-pill" :class="{ 'is-active': mode === 'ranking' }">伏笔排行 ({{ ranking.length }})</button>
```

排行视图模板（在 `v-else` 的 `chapters` 列表后面加 `v-if="mode === 'ranking'"` 分支）：

```html
<div v-if="mode === 'ranking'" class="space-y-2">
  <div v-if="ranking.length === 0" class="glass-card p-8 text-center text-sm" style="color: var(--win-text-disabled)">
    暂无排行数据，请先运行最终总结
  </div>
  <div v- class="space-y-2">
    <div v-for="(item, idx) in ranking" :key="item.id" class="tl-card">
      <div class="flex items-start gap-3">
        <span class="text-xs font-medium mt-0.5 shrink-0" style="color: var(--win-text-disabled)">#{{ idx + 1 }}</span>
        <div class="flex-1">
          <p style="color: var(--win-text-primary)">{{ item.description }}</p>
          <div class="mt-1.5 text-xs flex gap-3 flex-wrap items-center" style="color: var(--win-text-secondary)">
            <span>分数: <strong>{{ item.composite_score }}</strong></span>
            <span>跨度: ch{{ item.first_seen_chapter }}→ch{{ item.last_seen_chapter }} ({{ item.span }}章)</span>
            <span>检测: {{ item.evidence_count }}次</span>
            <span v-if="item.status === 'active'" class="status-tag badge-blue" style="font-size: 11px">活跃</span>
            <span v-else-if="item.status === 'resolved'" class="status-tag badge-green" style="font-size: 11px">已回收</span>
            <span v-else-if="item.status === 'dormant'" class="status-tag badge-gray" style="font-size: 11px">休眠</span>
            <span v-if="item.evidence_count <= 1 && item.span === 0" class="status-tag badge-orange" style="font-size: 11px">⚠️ 仅检测1次</span>
          </div>
        </div>
        <div class="shrink-0 flex flex-col items-end gap-1">
          <span class="glass-badge" :class="importanceBadge[item.importance] || 'badge-gray'">{{ item.importance }}</span>
          <!-- 分数条 -->
          <div class="w-20 h-1.5 rounded-full overflow-hidden" style="background: var(--win-stroke)">
            <div class="h-full rounded-full" :style="{ width: Math.min(item.composite_score / 4, 100) + '%', background: 'var(--win-accent)' }"></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
```

注意：分数条宽度 `Math.min(score / 4, 100)` — 最高分约 399（imp=高 300 + conf=高 30 + evi=50 + span=49），除以 4 ≈ 100% 满格。

**改动 5d**: 排行视图不显示分类筛选和重要度筛选（排行本身就是按分数排，不需要额外筛选）。当 `mode === 'ranking'` 时隐藏这些控件。

### 6. 测试

**新文件**: `backend/tests/test_foreshadow_composite_score.py`

```python
"""伏笔组合因子排序 + composite_score 保留测试"""
import pytest
from backend.utils.foreshadow_ledger import ForeshadowItem, SCHEMA_VERSION


def test_foreshadow_item_has_composite_score():
    """ForeshadowItem 新增 composite_score 和 importance 字段"""
    item = ForeshadowItem(
        id="fs_001", description="test", 
        first_seen_chapter=1, first_seen_batch=0,
        last_seen_chapter=100, last_seen_batch=0,
    )
    assert item.composite_score == 0  # 默认值
    assert item.importance == "中"  # 默认值


def test_foreshadow_item_from_dict_backward_compat():
    """旧 ledger JSON（无 composite_score/importance）能正常反序列化"""
    old_data = {
        "id": "fs_001",
        "description": "test",
        "first_seen_chapter": 1,
        "first_seen_batch": 0,
        "last_seen_chapter": 100,
        "last_seen_batch": 0,
        "status": "active",
        "confidence": 0.8,
        "evidence_chapters": [1, 50],
        "source_type": "catalog_scan",
    }
    item = ForeshadowItem.from_dict(old_data)
    assert item.composite_score == 0
    assert item.importance == "中"
    assert item.id == "fs_001"


def test_schema_version_bumped():
    """SCHEMA_VERSION 升级到 3"""
    assert SCHEMA_VERSION == 3
```

### 7. agent.md 更新

**文件**: `agent.md`

在 §10 当前状态最后加：

```markdown
### 10.22 2026-08-27 伏笔组合因子排序 + 排行视图
- **组合因子排序**：`_build_foreshadow_catalog` sort_key 从 `imp×100 + conf×10 + evidence_count(≤99)` 改为 `imp×100 + conf×10 + evidence_count(≤50) + span(≤49)`。**原因**：纯 evidence_count 对早期隐蔽伏笔结构性歧视（好伏笔越隐蔽检测次数越低，越易被截断）。组合因子让"跨章时间范围"(span) 和"被检测次数"(evidence_count) 各贡献一半权重。
- **composite_score 保留**：sort_key 计算后不再 pop，改名 `composite_score` 保留到 catalog 条目 + ledger 条目。
- **ForeshadowItem 扩展**：加 `composite_score: int = 0` + `importance: str = "中"`，SCHEMA_VERSION 升到 3。
- **排行 API**：`GET /api/books/{id}/foreshadow_ranking` 从 ledger 读+算分+降序返回。
- **前端排行视图**：TimelinePage 加第三个 mode "伏笔排行"，按分数降序列表，含分数条+span+evidence_count+status+⚠️标注。旧数据（无 composite_score）实时计算兜底。
- **未改**：`max_foreshadow_catalog_high` 默认值仍为 500（用户可在 GUI 手动改到 1000）。
- **回归**：老 ledger JSON 反序列化兼容（新字段默认值 0/"中"）；排行端点对旧数据实时算分兜底。
```

---

## 验证清单

- [ ] `python -m pytest backend/tests/test_foreshadow_composite_score.py -v`
- [ ] `python -m pytest` 全量（不回归）
- [ ] `cd frontend && npm run build`（vue-tsc 0 错）
- [ ] `grep -RIn "backdrop-filter" frontend/src || true`（无）
- [ ] agent.md §10 更新

## 约束

- **dataclass 新字段必须给默认值**（`= 0` / `= "中"`），否则 `from_dict` 白名单构造对老 JSON 报 TypeError
- **前端不引入新依赖**：ECharts 6.1 已有，排行视图用纯 HTML+CSS 分数条
- **Win11 设计系统**：所有色值用 `--win-*` 变量，badge 类复用现有 `badge-*` / `status-tag` / `glass-badge`
- **切书请求守卫**：排行数据加载复用已有 `timelineSeq` 计数器守卫
- **不截断默认值**：不改 `max_foreshadow_catalog_high` 的默认值（用户自己在 GUI 改）
