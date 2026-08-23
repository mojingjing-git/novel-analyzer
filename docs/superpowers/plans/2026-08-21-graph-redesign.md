# 2026-08-21 角色关系图重构 Plan

> 目标：替换 GraphPage.vue 的环形 SVG（807 节点塌成毛线球），改用 ECharts force 力导向 + 章节范围切片 + Top-N 截断。
> 保留 `backend/utils/character_graph.py`（戴森球独立 HTML 导出）不动。
> 工作区：`F:\AI\小说分析器`

---

## 0. 背景与根因

**现状代码定位：**

- `frontend/src/pages/GraphPage.vue`（191 行）：手写环形 SVG
  - `nodePositions` 计算（第 43-57 行）：`(i/n) * 2π` 环形摆位，半径 `min(250, n*8+100)`
  - 867 节点 → 半径截到 250px → 圆周 ~1570px → 每节点弧长仅 ~1.8px → 全糊
  - 标签硬 11px 全显示，不避让；边只用 stroke-width，没参与布局
- `backend/services/viz_service.py` `graph_data()`（第 91-136 行）：全量返回 nodes/edges，**无截断、无章节过滤**
- `backend/api/routes_viz.py` `get_graph`（第 40-48 行）：路由没接 query 参数
- `frontend/package.json`：当前依赖只有 `vue ^3.5.13` + `vue-router ^4.5.0`，**无任何图表库**

**根因**：GraphPage.vue 用 60 行手写环形 SVG，不是网络图布局算法。

---

## 1. 技术选型（已定）

- **ECharts 5.5+**：`series-graph` + `force` layout
  - 按需引入 `echarts/core` + `echarts/charts/GraphChart` + `echarts/components/TooltipComponent` + `echarts/components/DataZoomComponent` + `echarts/components/LegendComponent` + `echarts/renderers/SVGRenderer`，控制体积
  - WebView2（Chrome 100+ 内核）完全兼容
- **章节范围切片**：后端加 `chapter_start` / `chapter_end` query
- **Top-N 截断 + 边权阈值**：后端过滤，前端可调
- **Win11 Fluent 主题**：手动注入 ECharts color palette，不引默认主题

---

## 2. 后端改动

### 2.1 `backend/services/viz_service.py` — `graph_data()` 加参数

**当前签名**（第 91 行）：
```python
def graph_data(output_dir: Path) -> Dict[str, Any]:
```

**改为**：
```python
def graph_data(
    output_dir: Path,
    chapter_start: Optional[int] = None,
    chapter_end: Optional[int] = None,
    min_edge_weight: int = 1,
    max_nodes: int = 200,
    min_node_count: int = 1,
) -> Dict[str, Any]:
```

**逻辑改动（在现有聚合逻辑基础上叠加，不破坏原数据流）：**

1. **章节过滤**（在 `for result in results:` 循环前过滤 results）：
   ```python
   if chapter_start is not None or chapter_end is not None:
       results = [r for r in results
                  if (chapter_start is None or r.chapter_number >= chapter_start)
                  and (chapter_end is None or r.chapter_number <= chapter_end)]
   ```

2. **节点 count 阈值过滤**（在聚合完成后）：
   ```python
   nodes_map = {k: v for k, v in nodes_map.items()
                if v["event_count"] >= min_node_count}
   ```

3. **边权重阈值过滤**（在 nodes 过滤后）：
   ```python
   edges_map = {k: v for k, v in edges_map.items()
                if v["weight"] >= min_edge_weight
                and v["source"] in nodes_map
                and v["target"] in nodes_map}
   ```

4. **Top-N 截断**（按 event_count 降序，超出 max_nodes 的节点剔除，并连带剔除其所有边）：
   ```python
   if len(nodes_map) > max_nodes:
       sorted_names = sorted(nodes_map.items(),
                             key=lambda x: x[1]["event_count"], reverse=True)
       keep = {n for n, _ in sorted_names[:max_nodes]}
       nodes_map = {k: v for k, v in nodes_map.items() if k in keep}
       edges_map = {k: v for k, v in edges_map.items()
                    if v["source"] in keep and v["target"] in keep}
   ```

5. **返回值新增字段**（前端展示用）：
   ```python
   return {
       "nodes": [...],
       "edges": [...],
       "total_characters": <原始未截断的角色总数>,
       "total_edges": <原始未截断的边总数>,
       "filtered": {
           "chapter_start": chapter_start,
           "chapter_end": chapter_end,
           "min_edge_weight": min_edge_weight,
           "max_nodes": max_nodes,
           "min_node_count": min_node_count,
       },
       "chapter_range": {
           "min": <全书最小章号>,
           "max": <全书最大章号>,
       },
   }
   ```
   注意：要在过滤前记录 `total_characters`/`total_edges`/`chapter_range`，过滤后再组装 nodes/edges。

### 2.2 `backend/api/routes_viz.py` — 路由加 query

**当前**（第 40-48 行）：
```python
@router.get("/graph/{book_id}")
async def get_graph(book_id: str) -> dict:
    try:
        return {"book_id": book_id, **await asyncio.to_thread(viz_service.graph_data, _get_output_dir(book_id))}
```

**改为**：
```python
from fastapi import Query

@router.get("/graph/{book_id}")
async def get_graph(
    book_id: str,
    chapter_start: int | None = Query(None, ge=1),
    chapter_end: int | None = Query(None, ge=1),
    min_edge_weight: int = Query(1, ge=1),
    max_nodes: int = Query(200, ge=10, le=1000),
    min_node_count: int = Query(1, ge=1),
) -> dict:
    try:
        data = await asyncio.to_thread(
            viz_service.graph_data,
            _get_output_dir(book_id),
            chapter_start, chapter_end,
            min_edge_weight, max_nodes, min_node_count,
        )
        return {"book_id": book_id, **data}
```

**注意 FastAPI 版本**：用 `int | None` 还是 `Optional[int]` 取决于项目 Python 版本。项目要求 3.11+，可用 `int | None`；但若 settings.py 等已有文件用 `Optional`，则保持一致用 `Optional[int]`（查 `backend/config/settings.py` 确认风格后选用一致写法）。

### 2.3 不动项

- `backend/utils/character_graph.py`（戴森球独立 HTML 导出）—— 保留，作用场景不同
- `backend/utils/aggregate_utils.py` 的 `JSONAggregator.load_all_chapters()` —— 复用，不碰

---

## 3. 前端改动

### 3.1 装依赖

```bash
cd frontend
npm install echarts
```

- 装 `echarts@^5.5`（写本计划时最新稳定版）
- 不装 `vue-echarts` 包装层（避免多一层抽象、版本耦合；直接 `import * as echarts from 'echarts/core'` + 按需注册）

### 3.2 `frontend/src/api/client.ts` — `getGraph` 加参数

**当前**（第 367 行）：
```typescript
getGraph: (book_id: string) => request<{ nodes: unknown[]; edges: unknown[] }>(`/api/viz/graph/${book_id}`),
```

**改为**：
```typescript
getGraph: (book_id: string, params?: {
  chapter_start?: number
  chapter_end?: number
  min_edge_weight?: number
  max_nodes?: number
  min_node_count?: number
}) => {
  const query = params
    ? '?' + Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null)
        .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
        .join('&')
    : ''
  return request<{
    nodes: GraphNode[]
    edges: GraphEdge[]
    total_characters: number
    total_edges: number
    filtered: Record<string, unknown>
    chapter_range: { min: number; max: number }
  }>(`/api/viz/graph/${book_id}${query}`)
},
```

并在文件顶部补 `GraphNode` / `GraphEdge` interface（从 GraphPage.vue 搬过来，统一管理）：
```typescript
export interface GraphNode {
  id: string
  name: string
  event_count: number
  chapters?: number[]
}
export interface GraphEdge {
  source: string
  target: string
  weight: number
}
```

### 3.3 `frontend/src/pages/GraphPage.vue` — 整页重写

**整页重写，参考骨架如下。要严格遵循 Win11 Fluent 设计系统（`--win-*` 变量）。**

#### 3.3.1 功能清单

1. **ECharts force 力导向图**：`series.type='graph'`，`layout='force'`
2. **缩放拖拽**：`roam: true`
3. **邻接高亮**：`emphasis.focus: 'adjacency'`
4. **标签避让**：`labelLayout: { hideOverlap: true }`
5. **标签按 zoom 淡入**：监听 `datazoom` 事件，zoom 比例 < 0.5 时 `label.show=false`
6. **节点大小**：按 `event_count` 线性映射 `symbolSize`，范围 [12, 48]
7. **边粗细**：按 `weight` 线性映射 `lineStyle.width`，范围 [0.5, 3]
8. **边透明度**：按 `weight` 映射 `lineStyle.opacity`，范围 [0.15, 0.6]
9. **社区着色**：前端简单按度数分桶（top 10% / 30% / 50% / 其他）四色，不调后端算法（避免引入复杂度）；或按 event_count 分桶
10. **Tooltip**：hover 节点显示「名称 / 出场章数 / 关联数」；hover 边显示「A - B / 共现 N 章」
11. **章节范围双 slider**：`dataZoom` 组件 type='slider'，映射到 chapter_start/end，change 时重新请求后端
12. **Top-N 输入框**：默认 200，可选 50/100/200/300/500，change 时重新请求
13. **边权阈值输入框**：默认 1，可选 1/2/3/5，change 时重新请求
14. **右侧关联角色面板**：点节点 → 右侧显示该角色关联列表（复用现有 `selectedRelated` 逻辑），面板宽度 256px
15. **"跳转角色卡片"按钮**：右侧面板底部一个按钮，点击 `router.push('/characters?id=' + selected)`（需确认 CharacterCardPage 路由是否支持 query；若不支持，先做按钮+alert 占位，后续再接）

#### 3.3.2 ECharts option 骨架

```typescript
const option = {
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'item',
    backgroundColor: 'var(--win-layer)',  // 注：ECharts 不吃 CSS 变量，需在 JS 里读 computed style 取实色
    borderColor: '#E5E5E5',
    textStyle: { color: '#000', fontSize: 12 },
  },
  series: [{
    type: 'graph',
    layout: 'force',
    roam: true,
    label: { show: true, position: 'right', fontSize: 11, color: '#000' },
    labelLayout: { hideOverlap: true },
    emphasis: {
      focus: 'adjacency',
      lineStyle: { width: 3, opacity: 1 },
      label: { fontSize: 13, fontWeight: 'bold' },
    },
    force: {
      repulsion: 120,
      edgeLength: [30, 120],
      gravity: 0.1,
      layoutAnimation: true,
    },
    data: nodes.value.map(n => ({
      id: n.id,
      name: n.name,
      value: n.event_count,
      symbolSize: 12 + (n.event_count / maxCount) * 36,
      category: bucketByCount(n.event_count, maxCount),
      label: { show: true },
    })),
    links: edges.value.map(e => ({
      source: e.source,
      target: e.target,
      value: e.weight,
      lineStyle: {
        width: 0.5 + (e.weight / maxWeight) * 2.5,
        opacity: 0.15 + (e.weight / maxWeight) * 0.45,
      },
    })),
    categories: [
      { name: '核心', itemStyle: { color: '#0067C0' } },     // --win-accent
      { name: '主要', itemStyle: { color: '#0F7B0F' } },     // --win-success
      { name: '次要', itemStyle: { color: '#9D5D00' } },     // --win-warning
      { name: '边缘', itemStyle: { color: '#D0D0D0' } },     // --win-stroke-strong
    ],
    lineStyle: { color: '#D0D0D0', curveness: 0.1 },
  }],
}
```

**颜色取值注意**：ECharts 不吃 CSS 变量。两种方案：
- 方案 A（推荐）：组件 `onMounted` 时 `getComputedStyle(document.documentElement)` 读 `--win-accent` 等，转成实色 hex/rgba 注入 option
- 方案 B：直接硬编码 Win11 调色板 hex（已在上文给出），不读 CSS 变量。**选 B**，简单可靠；颜色值与 main.css 严格一致即可。

#### 3.3.3 组件结构

```vue
<script setup lang="ts">
import { ref, watch, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent, DataZoomComponent, LegendComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import BookSelector from '../components/BookSelector.vue'
import { api, type GraphNode, type GraphEdge } from '../api/client'
import { useRouter } from 'vue-router'

echarts.use([GraphChart, TooltipComponent, DataZoomComponent, LegendComponent, SVGRenderer])

const router = useRouter()
const bookId = ref('')
const nodes = ref<GraphNode[]>([])
const edges = ref<GraphEdge[]>([])
const selected = ref<string | null>(null)
const totalCharacters = ref(0)
const totalEdges = ref(0)
const chapterRange = ref<{ min: number; max: number }>({ min: 1, max: 1 })
const chapterRangeSelected = ref<[number, number]>([1, 1])
const maxNodes = ref(200)
const minEdgeWeight = ref(1)
const loading = ref(false)
const chartContainer = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

// 主题色（与 main.css --win-* 一致）
const COLORS = {
  bg: 'transparent',
  accent: '#0067C0',
  success: '#0F7B0F',
  warning: '#9D5D00',
  strokeStrong: '#D0D0D0',
  textPrimary: 'rgba(0,0,0,1)',
  textSecondary: 'rgba(0,0,0,0.6)',
}

async function loadData() {
  if (!bookId.value) return
  loading.value = true
  try {
    const [cs, ce] = chapterRangeSelected.value
    const res = await api.getGraph(bookId.value, {
      chapter_start: cs,
      chapter_end: ce,
      max_nodes: maxNodes.value,
      min_edge_weight: minEdgeWeight.value,
    })
    nodes.value = res.nodes
    edges.value = res.edges
    totalCharacters.value = res.total_characters
    totalEdges.value = res.total_edges
    chapterRange.value = res.chapter_range
    // 首次加载把 slider 拉到全书范围
    if (chapterRangeSelected.value[0] === 1 && chapterRangeSelected.value[1] === 1) {
      chapterRangeSelected.value = [res.chapter_range.min, res.chapter_range.max]
    }
    renderChart()
  } catch (e) {
    nodes.value = []; edges.value = []
    console.error('加载关系图失败:', e)
  } finally {
    loading.value = false
  }
}

function renderChart() {
  if (!chart || nodes.value.length === 0) return
  const maxCount = Math.max(...nodes.value.map(n => n.event_count || 1), 1)
  const maxWeight = Math.max(...edges.value.map(e => e.weight || 1), 1)
  // ... 组装 option，如 3.3.2 骨架
  chart.setOption(option, true)  // true = notMerge，全量替换
}

function bucketByCount(count: number, maxCount: number): number {
  if (maxCount === 0) return 3
  const ratio = count / maxCount
  if (ratio > 0.7) return 0  // 核心
  if (ratio > 0.4) return 1  // 主要
  if (ratio > 0.15) return 2 // 次要
  return 3                    // 边缘
}

onMounted(() => {
  if (chartContainer.value) {
    chart = echarts.init(chartContainer.value, null, { renderer: 'svg' })
    chart.on('click', (params: any) => {
      if (params.dataType === 'node') {
        selected.value = selected.value === params.data.id ? null : params.data.id
      }
    })
    chart.on('datazoom', () => {
      // zoom < 0.5 隐藏标签
      const zoom = chart?.getOption().dataZoom?.[0]?.start ?? 50
      // 实际用 chart.getOption() 拿 roam 状态，或用 echarts 实例的 transform
      // 简化：保留 labelLayout hideOverlap 即可，不强求 zoom 淡入
    })
    window.addEventListener('resize', handleResize)
  }
})

function handleResize() {
  chart?.resize()
}

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})

watch(bookId, () => {
  selected.value = null
  chapterRangeSelected.value = [1, 1]  // 重置，等加载后拉到全书
  loadData()
})

// 参数变化防抖
let debounceTimer: ReturnType<typeof setTimeout>
watch([chapterRangeSelected, maxNodes, minEdgeWeight], () => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(loadData, 400)
})

const selectedRelated = computed(() => {
  if (!selected.value) return []
  const related: { name: string; weight: number }[] = []
  for (const edge of edges.value) {
    if (edge.source === selected.value) {
      const n = nodes.value.find(x => x.id === edge.target)
      related.push({ name: n?.name || edge.target, weight: edge.weight })
    } else if (edge.target === selected.value) {
      const n = nodes.value.find(x => x.id === edge.source)
      related.push({ name: n?.name || edge.source, weight: edge.weight })
    }
  }
  return related.sort((a, b) => b.weight - a.weight)
})

const selectedNode = computed(() => {
  if (!selected.value) return null
  return nodes.value.find(n => n.id === selected.value) || null
})
</script>
```

#### 3.3.4 Template 骨架

```vue
<template>
  <div class="space-y-6">
    <h2 class="section-title">角色关系图</h2>
    <BookSelector v-model="bookId" />

    <div v-if="!bookId" class="glass-card p-8 text-center text-sm" style="color: var(--win-text-disabled)">
      请选择书目
    </div>
    <div v-else-if="loading && nodes.length === 0" class="glass-card p-8 text-center text-sm" style="color: var(--win-text-secondary)">
      加载中...
    </div>
    <div v-else-if="nodes.length === 0" class="glass-card p-8 text-center text-sm" style="color: var(--win-text-disabled)">
      暂无数据
    </div>
    <div v-else class="space-y-4">
      <!-- 工具栏 -->
      <div class="glass-card p-4 flex flex-wrap items-center gap-4 text-sm">
        <div>
          <span style="color: var(--win-text-secondary)">角色:</span>
          <span class="font-medium ml-1">{{ nodes.length }}</span>
          <span v-if="totalCharacters > nodes.length" style="color: var(--win-text-disabled)">
            / {{ totalCharacters }}（已截断 Top-{{ maxNodes }}）
          </span>
        </div>
        <div>
          <span style="color: var(--win-text-secondary)">关系:</span>
          <span class="font-medium ml-1">{{ edges.length }}</span>
        </div>
        <div class="flex items-center gap-2">
          <label style="color: var(--win-text-secondary)">章节范围:</label>
          <span class="font-medium">{{ chapterRangeSelected[0] }} - {{ chapterRangeSelected[1] }}</span>
        </div>
        <div class="flex items-center gap-2">
          <label style="color: var(--win-text-secondary)">Top-N:</label>
          <select v-model.number="maxNodes" class="glass-input" style="width: 80px">
            <option :value="50">50</option>
            <option :value="100">100</option>
            <option :value="200">200</option>
            <option :value="300">300</option>
            <option :value="500">500</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <label style="color: var(--win-text-secondary)">边权阈值:</label>
          <select v-model.number="minEdgeWeight" class="glass-input" style="width: 80px">
            <option :value="1">≥1</option>
            <option :value="2">≥2</option>
            <option :value="3">≥3</option>
            <option :value="5">≥5</option>
          </select>
        </div>
      </div>

      <!-- 章节范围 slider（用 input range 双滑块，或 ECharts dataZoom） -->
      <div class="glass-card p-4">
        <div class="text-xs mb-2" style="color: var(--win-text-secondary)">
          章节范围: {{ chapterRange.min }} - {{ chapterRange.max }}
        </div>
        <!-- 简单双 range 实现；或引入 nouislider；或直接两个 input number -->
        <div class="flex items-center gap-3">
          <input type="number" v-model.number="chapterRangeSelected[0]"
                 :min="chapterRange.min" :max="chapterRangeSelected[1]"
                 class="glass-input" style="width: 80px" />
          <span style="color: var(--win-text-disabled)">—</span>
          <input type="number" v-model.number="chapterRangeSelected[1]"
                 :min="chapterRangeSelected[0]" :max="chapterRange.max"
                 class="glass-input" style="width: 80px" />
          <button class="glass-button btn-sm" @click="chapterRangeSelected = [chapterRange.min, chapterRange.max]">
            全书
          </button>
        </div>
      </div>

      <!-- 图 + 右侧面板 -->
      <div class="flex gap-4">
        <div class="flex-1 glass-card p-2">
          <div ref="chartContainer" style="width: 100%; height: 560px;"></div>
        </div>
        <div v-if="selected && selectedNode" class="w-64 shrink-0">
          <div class="glass-card p-4 space-y-2">
            <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--win-stroke); letter-spacing: -0.01em">
              {{ selectedNode.name }}
            </h3>
            <div class="text-sm" style="color: var(--win-text-secondary)">
              出场章数: {{ selectedNode.event_count }}
            </div>
            <div class="text-sm font-medium pt-2" style="color: var(--win-text-primary)">
              关联角色 ({{ selectedRelated.length }}):
            </div>
            <div class="space-y-1 max-h-80 overflow-y-auto">
              <div v-for="(rel, idx) in selectedRelated" :key="idx" class="rel-row">
                <span style="color: var(--win-text-primary)">{{ rel.name }}</span>
                <span style="color: var(--win-text-disabled)">权重 {{ rel.weight }}</span>
              </div>
              <div v-if="!selectedRelated.length" class="text-xs" style="color: var(--win-text-disabled)">
                无关联
              </div>
            </div>
            <button class="glass-button btn-sm w-full mt-2" @click="goToCharacterCard">
              查看角色卡片
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

function goToCharacterCard() {
  if (selected.value) {
    router.push({ name: 'characters', query: { id: selected.value } })
    // 若路由不支持，先 console.warn；后续接 CharacterCardPage
  }
}
```

#### 3.3.5 Style

```vue
<style scoped>
/* ECharts 容器 */
:deep(.echarts-tooltip) {
  border-radius: var(--win-radius-control) !important;
}
/* 复用现有 .glass-card / .glass-input / .glass-button / .btn-sm / .rel-row */
.rel-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  background: var(--win-control-alt);
  border: 1px solid var(--win-stroke);
  padding: 4px 10px;
  border-radius: var(--win-radius-control);
}
</style>
```

---

## 4. 验证清单

### 4.1 前端

```bash
cd frontend && npm run build
```
- `vue-tsc` 0 错
- vite build 成功
- `grep -RIn "backdrop-filter" frontend/src` 应无结果（ECharts 默认不用，但检查）
- `grep -RIn "#007AFF\|#0A84FF" frontend/src` 应无 iOS 残留

### 4.2 后端

```bash
python -m py_compile backend/services/viz_service.py backend/api/routes_viz.py
```
- 无语法错误

### 4.3 功能冒烟（沙箱内无法跑，留给验收方）

- 选一本大书（节点 >200），确认：
  - 节点数被截到 max_nodes
  - 缩放拖拽正常
  - 点节点 → 右侧面板出现
  - 改 Top-N / 边权阈值 → 防抖后重新加载
  - 改章节范围 → 重新加载
  - "全书"按钮 → 恢复全范围

### 4.4 agent.md 更新

完成后更新 `agent.md`：
- 第 6.4 节 GraphPage 行数从 191 改为新的
- 第 10 节加 10.8 子节记录本次变更
- 第 5.2 节 viz_service.py 的 graph_data 补充新参数说明
- 第 5.4 节 routes_viz.py 的 graph 端点补充 query 参数

---

## 5. 注意事项

1. **不要碰** `backend/utils/character_graph.py`（戴森球独立 HTML 导出，作用场景不同）
2. **不要碰** `backend/utils/aggregate_utils.py`
3. **不要批量格式化**（CRLF/LF 历史差异）
4. **ECharts 按需引入**，不要 `import 'echarts'` 全量（体积大）
5. **颜色用硬编码 hex**（ECharts 不吃 CSS 变量），值必须与 main.css `--win-*` 一致
6. **修改前先备份**：`.bak_8.21_graph` 目录，gitignore 已覆盖 `.bak_*`
7. **修改后必须更新 agent.md**（第 9.6 节硬性约束）
8. **FastAPI Optional 写法**：查 `backend/config/settings.py` 确认项目风格，保持一致
9. **章节范围 slider**：用两个 `<input type="number">` 即可，不引第三方 slider 库
10. **防抖**：参数变化 400ms 防抖，避免频繁请求
11. **错误处理**：加载失败清空 nodes/edges（复用现有 F-1 模式）
12. **dispose**：组件卸载必须 `chart.dispose()` 防内存泄漏
13. **resize**：窗口缩放必须 `chart.resize()`

---

## 6. 交付物

1. `backend/services/viz_service.py` — graph_data 加参数
2. `backend/api/routes_viz.py` — 路由加 query
3. `frontend/package.json` + `frontend/package-lock.json` — 加 echarts 依赖
4. `frontend/src/api/client.ts` — getGraph 加参数 + GraphNode/GraphEdge interface
5. `frontend/src/pages/GraphPage.vue` — 整页重写
6. `.bak_8.21_graph/` — 备份（gitignore 覆盖）
7. `agent.md` — 更新相关章节
8. `npm run build` + `py_compile` 通过截图/日志
