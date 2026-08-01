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
        class="ml-2 px-2 py-0.5 rounded-full text-xs align-middle"
        :class="running ? 'bg-green-100 text-green-600' : 'bg-gray-100 text-gray-500'"
      >{{ running ? '运行中' : '已结束' }}</span>
    </h2>
    <div v-if="stats" class="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div class="px-4 py-2.5 text-sm font-semibold" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">分类 Token 汇总</div>
      <table class="glass-table">
        <thead class="text-gray-500 bg-gray-50">
          <tr>
            <th class="px-3 py-1.5 text-left">分类</th>
            <th class="px-3 py-1.5 text-right">输入 Tokens</th>
            <th class="px-3 py-1.5 text-right">输出 Tokens</th>
            <th class="px-3 py-1.5 text-right">总计</th>
            <th class="px-3 py-1.5 text-right">耗时(s)</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in categoryRows" :key="row.key" style="border-top: 1px solid var(--glass-border-subtle)">
            <td class="px-3 py-1.5">{{ row.label }}</td>
            <td class="px-3 py-1.5 text-right">{{ row.input.toLocaleString() }}</td>
            <td class="px-3 py-1.5 text-right">{{ row.output.toLocaleString() }}</td>
            <td class="px-3 py-1.5 text-right font-medium">{{ (row.input + row.output).toLocaleString() }}</td>
            <td class="px-3 py-1.5 text-right text-gray-500">{{ row.elapsed.toFixed(1) }}</td>
          </tr>
          <tr class="border-t-2 border-gray-300 bg-gray-50 font-medium">
            <td class="px-3 py-1.5">汇总</td>
            <td class="px-3 py-1.5 text-right">{{ totalIn.toLocaleString() }}</td>
            <td class="px-3 py-1.5 text-right">{{ totalOut.toLocaleString() }}</td>
            <td class="px-3 py-1.5 text-right">{{ (totalIn + totalOut).toLocaleString() }}</td>
            <td class="px-3 py-1.5 text-right text-gray-500">{{ totalElapsed }}s</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="stats" class="grid grid-cols-3 gap-3">
      <div class="bg-white border border-gray-200 rounded-lg p-3 text-center"><div class="text-xl font-bold text-cyan-500">{{ totalChapters }}</div><div class="text-xs" style="color: var(--color-system-gray)">总章数</div></div>
      <div class="bg-white border border-gray-200 rounded-lg p-3 text-center"><div class="text-xl font-bold text-green-500">{{ totalElapsed }}s</div><div class="text-xs" style="color: var(--color-system-gray)">累计耗时</div></div>
      <div class="bg-white border border-gray-200 rounded-lg p-3 text-center"><div class="text-xl font-bold text-blue-500">{{ totalOutputTokens.toLocaleString() }}</div><div class="text-xs" style="color: var(--color-system-gray)">输出 Tokens</div></div>
      <div class="bg-white border border-gray-200 rounded-lg p-3 text-center"><div class="text-xl font-bold text-purple-500">{{ avgTps }}</div><div class="text-xs" style="color: var(--color-system-gray)">平均 t/s</div></div>
      <div class="bg-white border border-gray-200 rounded-lg p-3 text-center"><div class="text-xl font-bold text-amber-500">{{ totalRetries }}</div><div class="text-xs" style="color: var(--color-system-gray)">重试次数</div></div>
      <div class="bg-white border border-gray-200 rounded-lg p-3 text-center"><div class="text-xl font-bold text-red-500">{{ totalFailedTokens.toLocaleString() }}</div><div class="text-xs" style="color: var(--color-system-gray)">失败Tokens·已扣费</div></div>
    </div>
    <div v-if="stats?.chapter_stats?.length" class="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div class="px-4 py-2.5 text-sm font-semibold" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">每章统计 ({{ stats.chapter_stats.length }}章)</div>
      <table class="glass-table" style="font-size: 12px">
        <thead class="text-gray-500 bg-gray-50">
          <tr>
            <th class="px-2 py-1 text-left">章号</th>
            <th class="px-2 py-1 text-left">耗时(s)</th>
            <th class="px-2 py-1 text-left">输入Tokens</th>
            <th class="px-2 py-1 text-left">输出Tokens</th>
            <th class="px-2 py-1 text-left">重试</th>
            <th class="px-2 py-1 text-left">失败Token</th>
            <th class="px-2 py-1 text-left">t/s</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in stats.chapter_stats" :key="s.chapter" style="border-top: 1px solid var(--glass-border-subtle)">
            <td class="px-2 py-1">{{ s.chapter }}</td>
            <td class="px-2 py-1">{{ s.elapsed?.toFixed(1) }}</td>
            <td class="px-2 py-1">{{ s.input_tokens }}</td>
            <td class="px-2 py-1">{{ s.output_tokens }}</td>
            <td class="px-2 py-1">{{ s.retries ?? 0 }}</td>
            <td class="px-2 py-1">{{ (s.failed_tokens ?? 0).toLocaleString() }}</td>
            <td class="px-2 py-1">{{ s.elapsed > 0 ? (s.output_tokens / s.elapsed).toFixed(1) : '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="!stats" class="text-gray-400 text-center py-8">暂无统计数据</div>
  </div>
</template>
