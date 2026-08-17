<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import BookSelector from '../components/BookSelector.vue'
import { api } from '../api/client'

interface Location {
  id: string
  name: string
  parent: string
  type: string
  description: string
  chapters: number[]
}

interface SpatialRel {
  from: string
  to: string
  relation: string
}

interface TreeNode extends Location {
  children: TreeNode[]
}

const bookId = ref('')
const locations = ref<Location[]>([])
const relationships = ref<SpatialRel[]>([])
const selected = ref<string | null>(null)

watch(bookId, async () => {
  if (!bookId.value) { locations.value = []; relationships.value = []; return }
  selected.value = null
  try {
    const res = await api.getMap(bookId.value)
    locations.value = res.locations as Location[]
    relationships.value = res.relationships as SpatialRel[]
  } catch (e) {
    // 切书失败清空：防止残留上一本书的地图数据（F-1）
    locations.value = []
    relationships.value = []
    console.error('加载地图失败，已清空:', e)
  }
})

const tree = computed(() => {
  const locMap = new Map<string, TreeNode>()
  const roots: TreeNode[] = []

  for (const loc of locations.value) {
    locMap.set(loc.name, { ...loc, children: [] })
  }

  for (const loc of locations.value) {
    const node = locMap.get(loc.name)!
    const parentName = loc.parent?.trim()
    // 过滤自引用（parent==name）：自引用会把节点塞进自己，导致 layoutNode 无限递归栈溢出
    if (parentName && parentName !== loc.name && locMap.has(parentName)) {
      locMap.get(parentName)!.children.push(node)
    } else {
      roots.push(node)
    }
  }

  return { roots, locMap }
})

const svgWidth = 800
const rowHeight = 50
const colWidth = 200

const layout = computed(() => {
  const positions: Record<string, { x: number; y: number; name: string; type: string; desc: string }> = {}
  const { roots } = tree.value

  function layoutNode(node: TreeNode, depth: number, row: { value: number }, visited: Set<string>) {
    // 防环 + 深度上限：LLM 数据出现环形引用（A→B→A）时避免无限递归栈溢出
    if (depth > 50 || visited.has(node.name)) return
    visited.add(node.name)
    const x = 20 + depth * colWidth
    const y = row.value * rowHeight + 20
    positions[node.name] = { x, y, name: node.name, type: node.type || '', desc: node.description || '' }
    row.value++
    if (node.children.length > 0) {
      for (const child of node.children) {
        layoutNode(child, depth + 1, row, visited)
      }
    }
  }

  let row = { value: 0 }
  for (const root of roots) {
    layoutNode(root, 0, row, new Set<string>())
  }

  const totalHeight = Math.max(row.value * rowHeight + 40, 200)
  return { positions, totalHeight }
})

const layoutEdges = computed(() => {
  const positions = layout.value.positions
  const edges: { x1: number; y1: number; x2: number; y2: number }[] = []
  for (const loc of locations.value) {
    const parentName = loc.parent?.trim()
    if (parentName && positions[parentName] && positions[loc.name]) {
      edges.push({
        x1: positions[parentName].x,
        y1: positions[parentName].y,
        x2: positions[loc.name].x,
        y2: positions[loc.name].y,
      })
    }
  }
  return edges
})

const spatialLines = computed(() => {
  const positions = layout.value.positions
  return relationships.value
    .filter(r => positions[r.from] && positions[r.to])
    .map(r => ({
      x1: positions[r.from].x,
      y1: positions[r.from].y,
      x2: positions[r.to].x,
      y2: positions[r.to].y,
      relation: r.relation,
    }))
})

const selectedLocation = computed(() => {
  if (!selected.value) return null
  return layout.value.positions[selected.value] || null
})

// 地点类型 → CSS 类（颜色走系统色 token，亮暗主题自适应）
function typeClass(t: string): string {
  return `type-${t || 'other'}`
}
</script>

<template>
  <div class="space-y-6">
    <h2 class="section-title">地图可视化</h2>
    <BookSelector v-model="bookId" />
    <div v-if="!bookId" class="glass-card p-8 text-center text-sm" style="color: var(--win-text-disabled)">请选择书目</div>
    <div v-else-if="locations.length === 0" class="glass-card p-8 text-center text-sm" style="color: var(--win-text-disabled)">暂无数据</div>
    <div v-else class="flex gap-4">
      <div class="flex-1 glass-card p-4 overflow-x-auto">
        <div class="text-sm mb-2" style="color: var(--win-text-secondary)">共 {{ locations.length }} 个地点, {{ relationships.length }} 条空间关系</div>
        <div class="viz-stage">
          <svg :width="svgWidth" :height="layout.totalHeight">
            <line
              v-for="(line, idx) in spatialLines"
              :key="'spatial' + idx"
              class="viz-spatial"
              :x1="line.x1" :y1="line.y1" :x2="line.x2" :y2="line.y2"
              stroke-dasharray="5,3" opacity="0.45" stroke-width="1.5"
            />
            <line
              v-for="(edge, idx) in layoutEdges"
              :key="'hier' + idx"
              class="viz-edge"
              :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2"
              stroke-width="1.5"
            />
            <g
              v-for="(pos, name) in layout.positions"
              :key="name"
              class="viz-loc"
              :class="[typeClass(pos.type), { 'is-selected': selected === name }]"
              @click="selected = selected === name ? null : name"
            >
              <circle :cx="pos.x" :cy="pos.y" :r="selected === name ? 10 : 7" stroke-width="2" />
              <text :x="pos.x + 12" :y="pos.y + 4" class="viz-label" style="font-size: 12px">{{ pos.name }}</text>
              <text v-if="pos.type" :x="pos.x + 12" :y="pos.y + 18" class="viz-type" style="font-size: 9px">{{ pos.type }}</text>
            </g>
          </svg>
        </div>
      </div>
      <div v-if="selectedLocation" class="w-64 shrink-0">
        <div class="glass-card p-4 space-y-2">
          <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--win-stroke); letter-spacing: -0.01em">{{ selectedLocation.name }}</h3>
          <div class="text-sm" style="color: var(--win-text-secondary)"><span style="color: var(--win-text-disabled)">类型:</span> {{ selectedLocation.type || '未知' }}</div>
          <div v-if="selectedLocation.desc" class="text-sm" style="color: var(--win-text-secondary)"><span style="color: var(--win-text-disabled)">描述:</span> {{ selectedLocation.desc }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.viz-stage {
  border-radius: var(--win-radius-container);
  background: var(--win-control-alt);
  border: 1px solid var(--win-stroke);
  overflow: auto;
}
.viz-edge {
  stroke: var(--win-stroke-strong);
  opacity: 0.6;
}
.viz-spatial {
  stroke: var(--win-warning);
  opacity: 0.55;
}
/* 地点类型配色：Win11 低饱和功能色，选中态强调色高亮 */
.viz-loc { cursor: pointer; --tc: var(--win-text-secondary); }
.viz-loc.type-region   { --tc: var(--win-success); }
.viz-loc.type-city     { --tc: var(--win-accent); }
.viz-loc.type-building { --tc: var(--win-info); }
.viz-loc.type-natural  { --tc: var(--win-info); }
.viz-loc.is-selected   { --tc: var(--win-accent); }
.viz-loc circle {
  fill: var(--win-layer);
  fill-opacity: 1;
  stroke: var(--tc);
  transition: fill var(--win-duration-fast) var(--win-ease), stroke var(--win-duration-fast) var(--win-ease);
}
.viz-loc:hover circle { stroke-width: 2.5; }
.viz-loc.is-selected circle {
  fill: var(--win-accent-soft);
  stroke-width: 2.5;
}
.viz-label { fill: var(--win-text-primary); }
.viz-type { fill: var(--win-text-disabled); }
</style>
