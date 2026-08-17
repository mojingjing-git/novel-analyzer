<script setup lang="ts">
import { computed } from 'vue'

interface CategoryStat {
  input_tokens?: number
  output_tokens?: number
  elapsed?: number
  [key: string]: any
}

const props = defineProps<{
  categories?: Record<string, CategoryStat>
  compact?: boolean
}>()

const totalIn = computed(() => {
  if (!props.categories) return 0
  return Object.values(props.categories).reduce((s, c) => s + (c.input_tokens || 0), 0)
})
const totalOut = computed(() => {
  if (!props.categories) return 0
  return Object.values(props.categories).reduce((s, c) => s + (c.output_tokens || 0), 0)
})
const total = computed(() => totalIn.value + totalOut.value)

const labels: Record<string, string> = {
  chapter: '章节', rolling: '滚动', batch: '卷摘要', final: '最终', foreshadow: '伏笔',
}
const categoryColors: Record<string, string> = {
  chapter: 'badge-blue', rolling: 'badge-purple', batch: 'badge-teal',
  final: 'badge-orange', foreshadow: 'badge-red',
}

function fmt(n: number) {
  if (n >= 10000) return `${(n / 1000).toFixed(1)}k`
  return n.toLocaleString()
}
</script>

<template>
  <div v-if="!compact" class="badge-row">
    <span class="glass-badge badge-blue total-badge">
      <strong>{{ fmt(total) }}</strong>
      <span style="opacity: 0.7; font-weight: 400">Tokens</span>
    </span>
    <span class="glass-badge badge-gray">入 {{ fmt(totalIn) }}</span>
    <span class="glass-badge badge-orange">出 {{ fmt(totalOut) }}</span>
    <span
      v-for="(cat, key) in categories"
      :key="key"
      class="glass-badge"
      :class="categoryColors[key] || 'badge-gray'"
    >
      {{ labels[key] || key }}: {{ fmt((cat.input_tokens || 0) + (cat.output_tokens || 0)) }}
    </span>
  </div>
  <span v-else class="compact-row">
    <span class="glass-badge badge-blue" style="padding: 2px 8px; font-size: 11px">
      <strong>{{ fmt(total) }}</strong>
    </span>
    <span class="compact-meta">({{ fmt(totalIn) }} / {{ fmt(totalOut) }})</span>
  </span>
</template>

<style scoped>
.badge-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.total-badge {
  font-size: 12px;
  /* 4px 12px 对齐 4 倍数（原 3px 10px 不合规） */
  padding: 4px 12px;
}
.compact-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
}
.compact-meta {
  color: var(--win-text-secondary);
}
</style>