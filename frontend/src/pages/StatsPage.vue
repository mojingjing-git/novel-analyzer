<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { api, type TokenStatsResponse } from '../api/client'

const stats = ref<TokenStatsResponse | null>(null)
const running = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

async function refresh() {
  try {
    // 运行状态与统计分离：结束后也能查看本次完整统计
    const status = await api.analysisStatus()
    running.value = status?.running ?? false
    stats.value = await api.getTokenStats()
  } catch (e) { console.error(e) }
}
onMounted(() => { refresh(); timer = setInterval(refresh, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })

const categoryRows = computed(() => {
  if (!stats.value?.categories) return []
  const labels: Record<string, string> = { chapter: '章节分析', rolling: '滚动总结', batch: '卷摘要', final: '最终报告', foreshadow: '伏笔分析' }
  return Object.entries(stats.value.categories).map(([key, val]) => ({
    key, label: labels[key] || key, input: val.input_tokens || 0, output: val.output_tokens || 0, elapsed: (val as { elapsed?: number }).elapsed || 0,
  }))
})

const totalIn = computed(() => categoryRows.value.reduce((s, r) => s + r.input, 0))
const totalOut = computed(() => categoryRows.value.reduce((s, r) => s + r.output, 0))
const totalElapsed = computed(() => stats.value?.elapsed?.toFixed(0) || '0')
const totalChapters = computed(() => stats.value?.chapter_stats?.length || 0)
const totalOutputTokens = computed(() => stats.value?.chapter_stats?.reduce((s, c) => s + (c.output_tokens || 0), 0) || 0)
const totalRetries = computed(() => stats.value?.total_retries || 0)
const totalFailedTokens = computed(() => stats.value?.total_failed_tokens || 0)
const cacheRate = computed(() => {
  const cached = stats.value?.cached_tokens || 0
  const denominator = cached + totalIn.value
  return denominator > 0 ? `${((cached / denominator) * 100).toFixed(1)}%` : '—'
})
const cachedTokens = computed(() => (stats.value?.cached_tokens || 0).toLocaleString())
const avgTps = computed(() => {
  const chapters = stats.value?.chapter_stats || []
  const totalTime = chapters.reduce((s, c) => s + (c.elapsed || 0), 0)
  const totalOut = chapters.reduce((s, c) => s + (c.output_tokens || 0), 0)
  return totalTime > 0 ? (totalOut / totalTime).toFixed(1) : '0'
})
</script>

<template>
  <div class="space-y-4 p-4">
    <h2 class="section-title">
      统计面板
      <span
        class="glass-badge ml-2 align-middle"
        :class="running ? 'badge-green' : 'badge-gray'"
        style="font-size: 11px"
      >{{ running ? '运行中' : '已结束' }}</span>
    </h2>

    <div v-if="stats" class="glass-card overflow-hidden">
      <div class="px-4 py-2.5 text-sm font-semibold" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">分类 Token 汇总</div>
      <table class="glass-table">
        <thead>
          <tr>
            <th class="text-left">分类</th>
            <th class="text-right">输入 Tokens</th>
            <th class="text-right">输出 Tokens</th>
            <th class="text-right">总计</th>
            <th class="text-right">耗时(s)</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in categoryRows" :key="row.key">
            <td>{{ row.label }}</td>
            <td class="text-right">{{ row.input.toLocaleString() }}</td>
            <td class="text-right">{{ row.output.toLocaleString() }}</td>
            <td class="text-right font-medium">{{ (row.input + row.output).toLocaleString() }}</td>
            <td class="text-right" style="color: var(--text-secondary)">{{ row.elapsed.toFixed(1) }}</td>
          </tr>
          <tr class="stat-total-row">
            <td class="font-semibold">汇总</td>
            <td class="text-right font-semibold">{{ totalIn.toLocaleString() }}</td>
            <td class="text-right font-semibold">{{ totalOut.toLocaleString() }}</td>
            <td class="text-right font-semibold">{{ (totalIn + totalOut).toLocaleString() }}</td>
            <td class="text-right" style="color: var(--text-secondary)">{{ totalElapsed }}s</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="stats" class="grid grid-cols-4 gap-3">
      <div class="glass-stat text-center">
        <div class="stat-num" style="color: var(--color-system-teal)">{{ totalChapters }}</div>
        <div class="glass-stat-label">总章数</div>
      </div>
      <div class="glass-stat text-center">
        <div class="stat-num" style="color: var(--color-system-green)">{{ totalElapsed }}s</div>
        <div class="glass-stat-label">累计耗时</div>
      </div>
      <div class="glass-stat text-center">
        <div class="stat-num" style="color: var(--color-system-blue)">{{ totalOutputTokens.toLocaleString() }}</div>
        <div class="glass-stat-label">输出 Tokens</div>
      </div>
      <div class="glass-stat text-center">
        <div class="stat-num" style="color: var(--color-system-purple)">{{ avgTps }}</div>
        <div class="glass-stat-label">平均 t/s</div>
      </div>
      <div class="glass-stat text-center">
        <div class="stat-num" style="color: var(--color-system-amber)">{{ totalRetries }}</div>
        <div class="glass-stat-label">重试次数</div>
      </div>
      <div class="glass-stat text-center">
        <div class="stat-num" style="color: var(--color-system-red)">{{ totalFailedTokens.toLocaleString() }}</div>
        <div class="glass-stat-label">失败Tokens·已扣费</div>
      </div>
      <div class="glass-stat text-center">
        <div class="stat-num" style="color: var(--color-system-blue)">{{ cacheRate }}</div>
        <div class="glass-stat-label">KV缓存命中率</div>
      </div>
      <div class="glass-stat text-center">
        <div class="stat-num" style="color: var(--color-system-teal)">{{ cachedTokens }}</div>
        <div class="glass-stat-label">缓存命中 Tokens</div>
      </div>
    </div>

    <div v-if="stats?.chapter_stats?.length" class="glass-card overflow-hidden">
      <div class="px-4 py-2.5 text-sm font-semibold" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">每章统计 ({{ stats.chapter_stats.length }}章)</div>
      <div class="stat-scroll">
        <table class="glass-table" style="font-size: 12px">
          <thead>
            <tr>
              <th class="text-left">章号</th>
              <th class="text-left">耗时(s)</th>
              <th class="text-left">输入Tokens</th>
              <th class="text-left">输出Tokens</th>
              <th class="text-left">重试</th>
              <th class="text-left">失败Token</th>
              <th class="text-left">t/s</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in stats.chapter_stats" :key="s.chapter">
              <td>{{ s.chapter }}</td>
              <td>{{ s.elapsed?.toFixed(1) }}</td>
              <td>{{ s.input_tokens }}</td>
              <td>{{ s.output_tokens }}</td>
              <td>{{ s.retries ?? 0 }}</td>
              <td>{{ (s.failed_tokens ?? 0).toLocaleString() }}</td>
              <td>{{ s.elapsed > 0 ? (s.output_tokens / s.elapsed).toFixed(1) : '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="!stats" class="glass-card p-8 text-center text-sm" style="color: var(--text-tertiary)">暂无统计数据</div>
  </div>
</template>

<style scoped>
.stat-num {
  font-size: 1.25rem;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.stat-total-row td {
  background: var(--glass-fill-subtle);
  border-top: 1px solid var(--glass-border-subtle);
}
.stat-scroll {
  max-height: 50vh;
  overflow: auto;
}
</style>
