<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import BookSelector from '../components/BookSelector.vue'
import { api } from '../api/client'

interface GraphNode {
  id: string
  name: string
  event_count: number
  chapters?: number[]
}

interface GraphEdge {
  source: string
  target: string
  weight: number
}

const bookId = ref('')
const nodes = ref<GraphNode[]>([])
const edges = ref<GraphEdge[]>([])
const selected = ref<string | null>(null)

watch(bookId, async () => {
  if (!bookId.value) { nodes.value = []; edges.value = []; return }
  selected.value = null
  try {
    const res = await api.getGraph(bookId.value)
    nodes.value = res.nodes as GraphNode[]
    edges.value = res.edges as GraphEdge[]
  } catch (e) {
    // 切书失败清空：防止残留上一本书的图被误当成当前书（F-1）
    nodes.value = []
    edges.value = []
    console.error('加载关系图失败，已清空:', e)
  }
})

const svgSize = 600
const centerX = 300
const centerY = 300

const nodePositions = computed(() => {
  const positions: Record<string, { x: number; y: number; r: number; name: string; count: number }> = {}
  const n = nodes.value.length
  if (n === 0) return positions
  const maxCount = Math.max(...nodes.value.map(node => node.event_count || 1), 1)
  const radius = Math.min(250, n * 8 + 100)
  nodes.value.forEach((node, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2
    const x = centerX + radius * Math.cos(angle)
    const y = centerY + radius * Math.sin(angle)
    const r = 10 + (node.event_count || 1) / maxCount * 20
    positions[node.id] = { x, y, r, name: node.name, count: node.event_count }
  })
  return positions
})

const svgEdges = computed(() => {
  const positions = nodePositions.value
  return edges.value
    .filter(e => positions[e.source] && positions[e.target])
    .map(e => ({
      x1: positions[e.source].x,
      y1: positions[e.source].y,
      x2: positions[e.target].x,
      y2: positions[e.target].y,
      weight: e.weight,
      source: e.source,
      target: e.target,
      opacity: Math.min(0.6, 0.1 + e.weight * 0.05),
    }))
})

function isRelated(edge: { source: string; target: string }): boolean {
  return !!selected.value && (edge.source === selected.value || edge.target === selected.value)
}

const selectedRelated = computed(() => {
  if (!selected.value) return []
  const positions = nodePositions.value
  const related: { name: string; weight: number }[] = []
  for (const edge of edges.value) {
    if (edge.source === selected.value && positions[edge.target]) {
      related.push({ name: positions[edge.target].name, weight: edge.weight })
    } else if (edge.target === selected.value && positions[edge.source]) {
      related.push({ name: positions[edge.source].name, weight: edge.weight })
    }
  }
  return related.sort((a, b) => b.weight - a.weight)
})
</script>

<template>
  <div class="space-y-4 p-4">
    <h2 class="section-title">角色关系图</h2>
    <BookSelector v-model="bookId" />
    <div v-if="!bookId" class="glass-card p-8 text-center text-sm" style="color: var(--text-tertiary)">请选择书目</div>
    <div v-else-if="nodes.length === 0" class="glass-card p-8 text-center text-sm" style="color: var(--text-tertiary)">暂无数据</div>
    <div v-else class="flex gap-4">
      <div class="flex-1 glass-card p-4">
        <div class="text-sm mb-2" style="color: var(--text-secondary)">共 {{ nodes.length }} 个角色, {{ edges.length }} 条关系</div>
        <div class="viz-stage">
          <svg :width="svgSize" :height="svgSize">
            <line
              v-for="(edge, idx) in svgEdges"
              :key="'edge' + idx"
              class="viz-edge"
              :class="{ 'is-related': isRelated(edge) }"
              :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2"
              :stroke-width="Math.min(3, 0.5 + edge.weight)"
              :opacity="edge.opacity"
            />
            <g
              v-for="(pos, id) in nodePositions"
              :key="id"
              class="viz-node"
              :class="{ 'is-selected': selected === id }"
              @click="selected = selected === id ? null : id"
            >
              <circle :cx="pos.x" :cy="pos.y" :r="pos.r" stroke-width="2" />
              <text :x="pos.x" :y="pos.y - pos.r - 4" text-anchor="middle" class="viz-label" style="font-size: 11px">{{ pos.name }}</text>
              <text :x="pos.x" :y="pos.y + 3" text-anchor="middle" class="viz-count" style="font-size: 9px">{{ pos.count }}</text>
            </g>
          </svg>
        </div>
      </div>
      <div v-if="selected" class="w-64 shrink-0">
        <div class="glass-card p-4 space-y-2">
          <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">{{ nodePositions[selected]?.name }}</h3>
          <div class="text-sm" style="color: var(--text-secondary)">事件数: {{ nodePositions[selected]?.count }}</div>
          <div class="text-sm font-medium pt-2" style="color: var(--text-primary)">关联角色:</div>
          <div class="space-y-1 max-h-60 overflow-y-auto">
            <div v-for="(rel, idx) in selectedRelated" :key="idx" class="rel-row">
              <span style="color: var(--text-primary)">{{ rel.name }}</span>
              <span style="color: var(--text-tertiary)">权重: {{ rel.weight }}</span>
            </div>
            <div v-if="!selectedRelated.length" class="text-xs" style="color: var(--text-tertiary)">无关联</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.viz-stage {
  border-radius: 14px;
  background: var(--glass-fill-subtle);
  border: 1px solid var(--glass-border-subtle);
  display: flex;
  justify-content: center;
  overflow: auto;
}
.viz-edge {
  stroke: var(--viz-edge);
  transition: stroke 200ms var(--ease-fluid);
}
.viz-edge.is-related {
  stroke: var(--color-system-teal);
}
.viz-node {
  cursor: pointer;
}
.viz-node circle {
  fill: var(--color-system-blue);
  fill-opacity: 0.28;
  stroke: var(--color-system-blue);
  transition: fill 200ms var(--ease-fluid), stroke 200ms var(--ease-fluid);
}
.viz-node:hover circle {
  fill-opacity: 0.45;
}
.viz-node.is-selected circle {
  fill: var(--color-system-teal);
  fill-opacity: 0.4;
  stroke: var(--color-system-teal);
}
.viz-label { fill: var(--text-primary); }
.viz-count { fill: var(--text-tertiary); }
.rel-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  background: var(--glass-fill-subtle);
  border: 1px solid var(--glass-border-subtle);
  padding: 4px 10px;
  border-radius: 8px;
}
</style>
