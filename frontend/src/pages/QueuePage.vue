<script setup lang="ts">
import Icon from '../components/Icon.vue'
import { ref, computed, onMounted, onUnmounted } from 'vue'
import LogConsole from '../components/LogConsole.vue'
import ChapterDetailPanel from '../components/ChapterDetailPanel.vue'
import BookSelector from '../components/BookSelector.vue'
import ProgressBar from '../components/ProgressBar.vue'
import TokenBadge from '../components/TokenBadge.vue'
import { api, type AnalysisStatus, type TokenStatsResponse } from '../api/client'
import { useProgressSocket, type ProgressMessage } from '../api/useProgressSocket'
import { useLogStore } from '../composables/useLogStore'

const status = ref<AnalysisStatus | null>(null)
const progress = ref({ current: 0, total: 0, eta: '' })
const tokens = ref<TokenStatsResponse | null>(null)
const busy = ref(false)
const error = ref('')
// 章节详情共享的书目与章节号（从独立的工具栏提升到此处，便于两个分栏内容框对齐）
const detailBookId = ref('')
const detailChapter = ref(0)
let pollTimer: ReturnType<typeof setInterval> | null = null

const { logs: storeLogs, add, clear } = useLogStore()
const logs = computed(() => storeLogs.value)
// 简化日志（过滤 debug 噪声）的条数，用于标题展示
const logsSimplifiedCount = computed(() => logs.value.filter((l) => l.kind !== 'debug').length)

// 最终总结进度反馈（后端通过 WS 广播 summary_progress，此前 QueuePage 完全忽略）
const summaryHint = ref('')
let summaryHideTimer: ReturnType<typeof setTimeout> | null = null
function showSummaryHint(text: string) {
  summaryHint.value = text
  if (summaryHideTimer) clearTimeout(summaryHideTimer)
  summaryHideTimer = setTimeout(() => { summaryHint.value = '' }, 10000)
}

function onMessage(msg: ProgressMessage) {
  switch (msg.type) {
    case 'progress':
      progress.value = {
        current: msg.payload.current as number,
        total: msg.payload.total as number,
        eta: (msg.payload.eta as string) || '',
      }
      break
    case 'block_done': refresh(); break
    case 'state_change': refresh(); break
    case 'token_stats': refreshTokens(); break
    case 'summary_progress': {
      const p = (msg as unknown as { payload?: Record<string, unknown> }).payload || {}
      const phaseLabel: Record<string, string> = {
        batch: '分卷分析+伏笔调和', recheck: '全书伏笔复检',
        style: '写作风格分析', report: '生成全书脉络报告', complete: '已完成',
      }
      const ph = phaseLabel[String(p.phase ?? '')] || String(p.phase ?? '')
     	const bd = p.batches_done ?? 0
      const tb = p.total_batches ?? 0
      const extra = p.message ? `（${String(p.message)}）` : ''
      showSummaryHint(`最终总结进行中${ph ? '：' + ph : ''} · 卷${bd}/${tb}${extra} — 详见「最终总结」页`)
      break
    }
  }
}

const { connected } = useProgressSocket(onMessage)

async function refresh() {
  try {
    const s = await api.analysisStatus()
    status.value = s
    // 刷新/重连时 WS 进度可能尚未到达，用 REST 状态兜底填充进度条，
    // 避免运行中刷新页面后进度条凭空消失（等待下一个 block_done 才出现）
    if (s?.running && progress.value.total === 0 && s.queue?.current_progress != null && s.queue.current_total) {
      progress.value = { current: s.queue.current_progress, total: s.queue.current_total, eta: '' }
    }
  } catch (e) { console.error(e) }
}
async function refreshTokens() { try { tokens.value = await api.getTokenStats() } catch (e) { console.error(e) } }

async function withBusy(fn: () => Promise<void>, label: string) {
  busy.value = true; error.value = ''
  try { await fn(); await refresh() }
  catch (e) { error.value = `${label}: ${(e as Error).message}`; add(error.value, 'error', 'analysis') }
  finally { busy.value = false }
}

function handleStart() { withBusy(async () => { await api.startAnalysis(); add('分析已启动', 'info', 'analysis') }, '启动分析') }
function handleStop() { withBusy(async () => { await api.stopAnalysis(); add('分析已停止', 'warn', 'analysis') }, '停止分析') }
function handleScanWorkspace() { withBusy(async () => { const res = await api.scanWorkspace(); add(`扫描完成，新增 ${res.added} 本小说`, 'info', 'analysis') }, '扫描工作区') }
function handleRemove(index: number) { withBusy(async () => { await api.removeQueueItem(index); add(`已移出队列项 #${index}`, 'info', 'analysis') }, '移出队列') }
function handleDelete(index: number) {
  if (!confirm('确定要删除该小说吗？文件将被移入系统回收站。')) return
  withBusy(async () => {
    await api.deleteBook(index)
    add(`已删除队列项 #${index} 并移入回收站`, 'warn', 'analysis')
  }, '删除小说')
}
function handleMoveUp(index: number) { withBusy(async () => { await api.moveQueueItemUp(index) }, '上移') }
function handleMoveDown(index: number) { withBusy(async () => { await api.moveQueueItemDown(index) }, '下移') }
function handleReset(index: number) { withBusy(async () => { await api.resetQueueItem(index); add(`已重置队列项 #${index} 为待处理`, 'info', 'analysis') }, '重跑') }
function handleClear() { withBusy(async () => { await api.clearQueue(); add('队列已清空', 'info', 'analysis') }, '清空队列') }

const statusBadgeClass: Record<string, string> = {
  pending: 'badge-gray', running: 'badge-blue', done: 'badge-green',
  failed: 'badge-red', skipped: 'badge-gray',
}

const statusLabels: Record<string, string> = {
  pending: '等待中', running: '运行中', done: '已完成', failed: '失败', skipped: '已跳过',
}

const currentBlockSize = computed(() => {
  if (!status.value?.running || !status.value?.items?.length) return 1
  // 取正在运行的那一项的 block_size，而非首项（多书混排时首项未必是运行中）
  const runningItem = status.value.items.find((i) => i.status === 'running') || status.value.items[0]
  return runningItem?.block_size || 1
})

onMounted(() => {
  refresh()
  pollTimer = setInterval(async () => {
    await refresh()
    if (status.value?.running) await refreshTokens()
  }, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (summaryHideTimer) clearTimeout(summaryHideTimer)
})
</script>

<template>
  <div class="space-y-4 p-4">
    <div class="flex items-center justify-between">
      <div>
        <h2 class="section-title">分析队列</h2>
        <p class="section-subtitle">管理待分析的小说并启动批量流水线</p>
      </div>
      <div class="flex gap-2">
        <button @click="handleScanWorkspace" :disabled="busy || status?.running" class="glass-button">扫描工作区</button>
        <button v-if="!status?.running" @click="handleStart" :disabled="busy" class="glass-button glass-button-primary"><Icon name="play" :size="12" /> 开始分析</button>
        <button v-else @click="handleStop" :disabled="busy" class="glass-button glass-button-danger"><Icon name="stop" :size="12" /> 停止</button>
      </div>
    </div>

    <div v-if="status?.running && progress.total > 0" class="glass-card p-4">
      <ProgressBar :current="progress.current" :total="progress.total" :eta="progress.eta" :block-size="currentBlockSize" label="进度" />
    </div>

    <div v-if="error" class="glass-tinted-red px-4 py-2 rounded-ios-md text-sm" style="color: var(--color-system-red)">{{ error }}</div>

    <div v-if="summaryHint" class="glass-tinted-blue px-4 py-2 rounded-ios-md text-sm" style="color: var(--color-system-blue)">
      {{ summaryHint }}
    </div>

    <div class="glass-card">
      <table class="glass-table">
        <thead>
          <tr>
            <th>书名</th>
            <th style="width: 100px">状态</th>
            <th style="width: 140px">进度</th>
            <th style="width: 220px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!status?.items?.length">
            <td colspan="4" style="text-align: center; padding: 32px; color: var(--color-system-gray)">队列为空</td>
          </tr>
          <tr v-for="(item, idx) in status?.items || []" :key="idx">
            <td style="font-weight: 500">{{ item.name }}</td>
            <td>
              <span class="glass-badge" :class="statusBadgeClass[item.status] || 'badge-gray'">
                {{ statusLabels[item.status] || item.status }}
              </span>
            </td>
            <td style="color: var(--color-system-gray)">
              <template v-if="item.block_size > 1">
                {{ item.completed_chapters }}/{{ Math.ceil(item.total_chapters / item.block_size) }} 块
                <span style="font-size: 10px; margin-left: 4px">({{ item.completed_chapters * item.block_size }}/{{ item.total_chapters }} 章)</span>
              </template>
              <template v-else>
                {{ item.completed_chapters }}/{{ item.total_chapters }}
                <span style="font-size: 10px; margin-left: 4px">章</span>
              </template>
            </td>
            <td>
              <div class="ops-row">
                <button
                  :class="['glass-button op-btn op-icon', { 'op-hidden': !(idx > 0 && !status?.running) }]"
                  @click="handleMoveUp(idx)"
                  :disabled="busy || status?.running || idx === 0"
                >
                  <Icon name="arrow_up" :size="12" />
                </button>
                <button
                  :class="['glass-button op-btn op-icon', { 'op-hidden': !(idx < (status?.items.length || 0) - 1 && !status?.running) }]"
                  @click="handleMoveDown(idx)"
                  :disabled="busy || status?.running || idx === (status?.items.length || 0) - 1"
                >
                  <Icon name="arrow_down" :size="12" />
                </button>
                <button
                  :class="['glass-button op-btn', { 'op-hidden': !(['done', 'failed', 'skipped'].includes(item.status) && !status?.running) }]"
                  @click="handleReset(idx)"
                  :disabled="busy || status?.running || !['done', 'failed', 'skipped'].includes(item.status)"
                >
                  重跑
                </button>
                <button
                  v-if="!status?.running && item.status !== 'running'"
                  @click="handleRemove(idx)"
                  class="glass-button op-btn"
                  :disabled="busy"
                >
                  移出队列
                </button>
                <button
                  v-if="!status?.running && item.status !== 'running'"
                  @click="handleDelete(idx)"
                  class="glass-button op-btn op-danger"
                  :disabled="busy"
                >
                  删除
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-between flex-wrap gap-3">
      <TokenBadge v-if="tokens?.categories" :categories="tokens.categories" />
      <a href="/api/analysis/logs" target="_blank" class="glass-button" style="font-size: 12px">下载日志</a>
    </div>

    <div class="split-grid">
      <!-- 左栏：章节选择（与详情同宽）+ 详情液态玻璃框 -->
      <div class="split-col">
        <div class="col-head">
          <div class="head-main">
            <span class="col-title">章节详情</span>
            <span class="col-sub2">{{ detailChapter > 0 ? `正在查看 第 ${detailChapter} 章` : '自动显示最新章节' }}</span>
          </div>
          <div class="cd-controls">
            <BookSelector v-model="detailBookId" class="cd-book" />
            <input
              v-model.number="detailChapter"
              type="number"
              min="0"
              placeholder="留空=最新"
              class="glass-input dt-input"
            />
          </div>
        </div>
        <ChapterDetailPanel :book-id="detailBookId" v-model:chapter="detailChapter" class="split-detail" />
      </div>

      <!-- 右栏：简化日志（过滤 debug），撑满整列（腾出的空间由其填补） -->
      <div class="split-col">
        <div class="col-head">
          <div class="head-main">
            <span class="col-title">
              实时日志（简）
              <span class="col-sub">({{ logsSimplifiedCount }} 条)</span>
            </span>
            <span class="col-sub2">完整日志见「设置」页底部</span>
          </div>
          <div class="flex items-center gap-3">
            <span class="glass-badge" :class="connected ? 'badge-green' : 'badge-gray'" style="font-size: 11px">
              {{ connected ? '● 已连接' : '○ 未连接' }}
            </span>
            <button @click="clear" class="glass-button" style="padding: 4px 10px; font-size: 12px">清空</button>
          </div>
        </div>
        <LogConsole :logs="logs" :simplified="true" class="split-log" />
      </div>
    </div>

    <div v-if="tokens?.chapter_stats?.length" class="glass-card">
      <div class="px-4 py-2.5 text-sm font-semibold" style="border-bottom: 1px solid var(--glass-border-subtle)">
        每章统计
        <span style="color: var(--color-system-gray); font-weight: 400">(最近 {{ Math.min(50, tokens.chapter_stats.length) }} 章 / 共 {{ tokens.chapter_stats.length }} 章)</span>
      </div>
      <table class="glass-table" style="border-radius: 0; border: none">
        <thead>
          <tr><th>章节</th><th>耗时(s)</th><th>输入 Tokens</th><th>输出 Tokens</th><th>t/s</th></tr>
        </thead>
        <tbody>
          <tr v-for="stat in tokens.chapter_stats.slice(-50)" :key="stat.chapter">
            <td>第 {{ stat.chapter }} 章</td>
            <td>{{ stat.elapsed?.toFixed(1) }}</td>
            <td>{{ stat.input_tokens }}</td>
            <td>{{ stat.output_tokens }}</td>
            <td>{{ stat.elapsed > 0 ? (stat.output_tokens / stat.elapsed).toFixed(1) : '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="status?.items?.length && !status?.running" class="flex justify-end">
      <button @click="handleClear" :disabled="busy" class="glass-button" style="color: var(--color-system-red); padding: 6px 14px">清空队列</button>
    </div>
  </div>
</template>

<style scoped>
/* 章节选择控件：位于左栏标题条内，与详情框同宽 */
.cd-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}
.cd-book {
  flex: 1;
  min-width: 0;
}
.dt-input {
  width: 110px;
  flex-shrink: 0;
}

/* 实时日志与章节详情各占一半宽度，等高且顶部对齐；工具栏移除后腾出的空间由日志整列撑满 */
.split-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  height: 64vh;
  min-height: 460px;
}
.split-col {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
/* 两个分栏使用同等高度的标题条，保证下方内容框上沿、下沿都对齐 */
.col-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 38px;
  margin-bottom: 10px;
}
.head-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.col-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-primary);
}
.col-sub {
  color: var(--color-system-gray);
  font-weight: 400;
  margin-left: 6px;
  font-size: 12px;
}
.col-sub2 {
  font-size: 11px;
  font-weight: 400;
  line-height: 1.3;
  color: var(--text-tertiary);
}
/* 让日志区撑满所在列（覆盖 LogConsole 自带的 max-height:320px） */
.split-log {
  flex: 1;
  min-height: 0;
  max-height: none !important;
  height: 100%;
}
/* 章节详情白框撑满所在列 */
.split-detail {
  flex: 1;
  min-height: 0;
}

/* 操作列：上移/下移/重跑 使用占位隐藏，保证「移出队列」和「删除」始终对齐 */
.ops-row {
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: flex-end;
}
.op-btn {
  padding: 4px 10px;
  font-size: 12px;
  white-space: nowrap;
}
.op-icon {
  padding: 4px 10px;
  min-width: 32px;
}
.op-hidden {
  visibility: hidden;
  pointer-events: none;
}
.op-danger {
  color: var(--color-system-red);
}
</style>