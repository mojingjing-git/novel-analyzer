<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import BookSelector from '../components/BookSelector.vue'
import LogConsole from '../components/LogConsole.vue'
import { api } from '../api/client'
import { useProgressSocket } from '../api/useProgressSocket'
import { useLogStore } from '../composables/useLogStore'

const bookId = ref('')
const useLlm = ref(true)
const limit = ref(50)
const running = ref(false)
const error = ref('')
const phase = ref('')
const styleContent = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

// 全局日志存储（跨页面持久化）
const { logs: storeLogs, add, clear } = useLogStore()
const logs = computed(() => storeLogs.value)

// 获取连接状态（WS 是单例，这里只是订阅）
const { connected } = useProgressSocket(() => {})

async function start() {
  if (!bookId.value) return
  error.value = ''
  styleContent.value = ''
  try {
    await api.startStyle(bookId.value, useLlm.value, limit.value)
    running.value = true
    add('风格分析已启动', 'info', 'style')
    startPolling()
  } catch (e) {
    error.value = (e as Error).message
    add(error.value, 'error', 'style')
  }
}

async function stop() {
  try {
    await api.stopStyle()
    running.value = false
    add('风格分析已停止', 'warn', 'style')
  } catch (e) { add((e as Error).message, 'error', 'style') }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const status = await api.styleStatus()
      phase.value = status.phase || ''
      if (!status.running) {
        running.value = false
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
        try {
          const res = await api.getStyleResult(bookId.value)
          styleContent.value = res.content
          add('风格分析完成', 'info', 'style')
        } catch {
          add('风格分析已完成，但结果文件不存在', 'warn', 'style')
        }
      }
    } catch (e) { console.error(e) }
  }, 2000)
}

async function loadResult() {
  if (!bookId.value) return
  try {
    const res = await api.getStyleResult(bookId.value)
    styleContent.value = res.content
  } catch (e) {
    add('尚无风格分析结果', 'warn', 'style')
  }
}

onMounted(() => {
  api.styleStatus().then(status => {
    phase.value = status.phase || ''
    if (status.running) {
      running.value = true
      startPolling()
    }
  })
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="space-y-4 p-4">
    <h2 class="section-title">风格分析</h2>
    <BookSelector v-model="bookId" />
    <div class="glass-card p-4 space-y-3">
      <label class="flex items-center gap-2 text-sm" style="color: var(--text-primary)">
        <input v-model="useLlm" type="checkbox" :disabled="running" />
        使用LLM（含七维评分）
      </label>
      <div class="flex items-center gap-2">
        <label class="text-sm w-20" style="color: var(--text-secondary)">分析章数:</label>
        <input v-model.number="limit" type="number" :disabled="running" class="glass-input w-24" />
      </div>
      <div class="flex gap-2">
        <button v-if="!running" @click="start" :disabled="!bookId" class="glass-button glass-button-primary">开始分析</button>
        <button v-else @click="stop" class="glass-button glass-button-danger">停止</button>
        <button v-if="!running && bookId" @click="loadResult" class="glass-button">加载结果</button>
        <span v-if="running && phase" class="glass-badge badge-blue self-center">{{ phase === 'computing_stats' ? '统计计算中' : phase === 'llm_extract' ? 'LLM 语义提取中' : phase === 'done' ? '已完成' : phase === 'error' ? '失败' : phase }}</span>
      </div>
    </div>
    <div v-if="error" class="glass-tinted-red px-4 py-2 rounded-ios-md text-sm">{{ error }}</div>
    <div>
      <div class="flex items-center justify-between mb-1">
        <span class="text-sm font-medium" style="color: var(--text-primary)">日志</span>
        <span class="glass-badge" :class="connected ? 'badge-green' : 'badge-gray'" style="font-size: 11px">{{ connected ? '● 已连接' : '○ 未连接' }}</span>
      </div>
      <LogConsole :logs="logs" />
    </div>
    <div v-if="styleContent" class="glass-card p-4">
      <h3 class="font-medium pb-2 mb-3" style="border-bottom: 1px solid var(--glass-border-subtle); color: var(--text-primary)">风格分析结果 (style.md)</h3>
      <pre class="text-sm whitespace-pre-wrap max-h-96 overflow-y-auto" style="color: var(--text-primary)">{{ styleContent }}</pre>
    </div>
  </div>
</template>
