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
  if (!bookId.value) return
  selected.value = null
  try {
    const res = await api.getMap(bookId.value)
    locations.value = res.locations as Location[]
    relationships.value = res.relationships as SpatialRel[]
  } catch (e) { console.error(e) }
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

const typeColors: Record<string, string> = {
  region: '#22c55e',
  city: '#3b82f6',
  building: '#a855f7',
  natural: '#10b981',
  other: '#6b7280',
}
</script>

<template>
  <div class="space-y-4 p-4">
    <h2 class="section-title">地图可视化</h2>
    <BookSelector v-model="bookId" />
    <div v-if="!bookId" class="text-gray-400">请选择书目</div>
    <div v-else-if="locations.length === 0" class="text-gray-400">暂无数据</div>
    <div v-else class="flex gap-4">
      <div class="flex-1 bg-white border border-gray-200 rounded-lg p-4 overflow-x-auto">
        <div class="text-sm text-gray-500 mb-2">共 {{ locations.length }} 个地点, {{ relationships.length }} 条空间关系</div>
        <svg :width="svgWidth" :height="layout.totalHeight" class="border border-gray-100 rounded">
          <line v-for="(line, idx) in spatialLines" :key="'spatial' + idx" :x1="line.x1" :y1="line.y1" :x2="line.x2" :y2="line.y2" stroke="#f97316" stroke-dasharray="5,3" opacity="0.4" stroke-width="1.5" />
          <line v-for="(edge, idx) in layoutEdges" :key="'hier' + idx" :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2" stroke="#ccc" stroke-width="1.5" />
          <g v-for="(pos, name) in layout.positions" :key="name" @click="selected = selected === name ? null : name" class="cursor-pointer">
            <circle :cx="pos.x" :cy="pos.y" :r="selected === name ? 10 : 7" :fill="selected === name ? '#66dae9' : (typeColors[pos.type] || '#22c55e')" fill-opacity="0.3" :stroke="selected === name ? '#66dae9' : (typeColors[pos.type] || '#22c55e')" stroke-width="2" />
            <text :x="pos.x + 12" :y="pos.y + 4" class="fill-gray-700" style="font-size: 12px">{{ pos.name }}</text>
            <text v-if="pos.type" :x="pos.x + 12" :y="pos.y + 18" class="fill-gray-400" style="font-size: 9px">{{ pos.type }}</text>
          </g>
        </svg>
      </div>
      <div v-if="selectedLocation" class="w-64 shrink-0">
        <div class="bg-white border border-gray-200 rounded-lg p-4 space-y-2">
          <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">{{ selectedLocation.name }}</h3>
          <div class="text-sm" style="color: var(--color-system-gray)"><span class="text-gray-400">类型:</span> {{ selectedLocation.type || '未知' }}</div>
          <div v-if="selectedLocation.desc" class="text-sm" style="color: var(--color-system-gray)"><span class="text-gray-400">描述:</span> {{ selectedLocation.desc }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
