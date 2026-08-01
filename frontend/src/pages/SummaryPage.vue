<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import BookSelector from '../components/BookSelector.vue'
import { api, type SummaryStatus } from '../api/client'
import { renderMarkdown } from '../utils/markdown'

// 总结进度的 4 个阶段（对齐旧版日志「最终总结」部分的粒度）
const SUMMARY_PHASES = [
  { key: 'batch', label: '分卷分析 + 伏笔调和', weight: 0.7 },
  { key: 'recheck', label: '全书伏笔复检', weight: 0.12 },
  { key: 'style', label: '写作风格分析', weight: 0.08 },
  { key: 'report', label: '生成全书脉络报告', weight: 0.1 },
]
const PHASE_LABELS: Record<string, string> = {
  starting: '准备中...',
  batch: '分卷分析 + 伏笔调和',
  recheck: '全书伏笔复检',
  style: '写作风格分析',
  report: '生成全书脉络报告',
  complete: '完成',
  stopped: '已停止',
  failed: '失败',
  idle: '空闲',
}

// ===== 共享书目 =====
const bookId = ref('')

// ===== 区块一：数据聚合 =====
const aggBusy = ref(false)
const aggFiles = ref<{ name: string; size_kb: number }[]>([])
const aggContent = ref<unknown>(null)
const aggIsJson = ref(false)
const aggError = ref('')

async function runAggregate() {
  aggBusy.value = true
  aggError.value = ''
  try { await api.runAggregate(bookId.value, false); await loadAggFiles() }
  catch (e) { aggError.value = (e as Error).message }
  finally { aggBusy.value = false }
}

async function loadAggFiles() {
  if (!bookId.value) { aggFiles.value = []; return }
  try { const res = await api.getAggregateFiles(bookId.value); aggFiles.value = res.files } catch (e) { console.error(e) }
}

async function viewAggFile(name: string) {
  try { const res = await api.getAggregateFile(bookId.value, name); aggContent.value = res.content; aggIsJson.value = res.is_json } catch (e) { alert((e as Error).message) }
}

async function exportExcel() {
  try { await api.exportAggregateExcel(bookId.value); alert('导出成功') } catch (e) { alert((e as Error).message) }
}

// ===== 区块二：最终总结 =====
const startCh = ref(1)
const endCh = ref(99999)
const batchSize = ref(30)
const concurrency = ref(2)
const summaryStatus = ref<SummaryStatus | null>(null)
const report = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

const reportHtml = computed(() => renderMarkdown(report.value))

// ===== 进度条（细化：4 阶段步骤 + 批次子进度 + 整体百分比 + 耗时）=====
const phaseLabel = computed(() => PHASE_LABELS[summaryStatus.value?.phase || 'idle'] || '准备中')

const phaseIndex = computed(() => {
  const p = summaryStatus.value?.phase
  if (!p || p === 'idle' || p === 'starting') return -1
  if (p === 'complete') return SUMMARY_PHASES.length // 全部完成
  const i = SUMMARY_PHASES.findIndex((x) => x.key === p)
  if (i >= 0) return i
  return -1 // failed / stopped：无法精确判断停在哪步，交给 banner 提示
})

function stepClass(i: number): string {
  if (phaseIndex.value > i) return 'done'
  if (phaseIndex.value === i) return 'active'
  return 'pending'
}

const overallPct = computed(() => {
  const s = summaryStatus.value
  if (!s) return 0
  if (s.phase === 'complete') return 100
  // 失败/停止：用已完成的卷进度近似（保住已取得的进度感）
  if ((s.phase === 'failed' || s.phase === 'stopped') && s.total_batches > 0) {
    return Math.round(SUMMARY_PHASES[0].weight * 100 * (s.batches_done / s.total_batches))
  }
  const i = SUMMARY_PHASES.findIndex((x) => x.key === s.phase)
  if (i < 0) return 0
  let pct = 0
  for (let k = 0; k < i; k++) pct += SUMMARY_PHASES[k].weight * 100
  if (s.phase === 'batch' && s.total_batches > 0) {
    pct += SUMMARY_PHASES[i].weight * 100 * (s.batches_done / s.total_batches)
  } else {
    pct += SUMMARY_PHASES[i].weight * 50 // 非批次阶段进行中，计该阶段权重的一半
  }
  return Math.min(100, Math.round(pct))
})

const batchPct = computed(() => {
  const s = summaryStatus.value
  if (!s || !s.total_batches) return 0
  return Math.round((s.batches_done / s.total_batches) * 100)
})

const elapsedText = computed(() => {
  const s = summaryStatus.value
  if (!s || !s.started_at) return ''
  const end = s.finished_at || Date.now() / 1000
  const secs = Math.max(0, Math.round(end - s.started_at))
  if (secs < 60) return `${secs}秒`
  return `${Math.floor(secs / 60)}分${secs % 60}秒`
})

const showProgress = computed(() => {
  const s = summaryStatus.value
  if (!s) return false
  if (s.running) return true
  return ['complete', 'failed', 'stopped'].includes(s.phase)
})

async function startSummary() {
  try { await api.startSummary(bookId.value, startCh.value, endCh.value, batchSize.value, concurrency.value) }
  catch (e) { alert((e as Error).message) }
}
async function stopSummary() { try { await api.stopSummary() } catch (e) { alert((e as Error).message) } }
async function refreshSummaryStatus() { try { summaryStatus.value = await api.summaryStatus() } catch (e) { console.error(e) } }
async function loadReport() {
  if (!bookId.value) return
  try { const res = await api.getBookReport(bookId.value); report.value = res.report } catch (e) { console.error(e) }
}

// 切换书目时，自动刷新两个区块
watch(bookId, async () => {
  aggFiles.value = []
  aggContent.value = null
  aggError.value = ''
  report.value = ''
  await loadAggFiles()
  await loadReport()
})

onMounted(() => { refreshSummaryStatus(); loadAggFiles(); loadReport(); pollTimer = setInterval(refreshSummaryStatus, 2000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<template>
  <div class="space-y-6 p-4">
    <div>
      <h2 class="section-title">聚合与总结</h2>
      <p class="section-subtitle">最终总结生成全书报告，数据聚合生成结构化结果（可二选一使用）</p>
    </div>

    <BookSelector v-model="bookId" />

    <!-- 区块一：最终总结（更常用，置顶） -->
    <section class="glass-card p-4 space-y-3">
      <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">最终总结</h3>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="flex items-center gap-2">
          <label class="text-sm shrink-0" style="color: var(--color-system-gray)">起始章:</label>
          <input v-model.number="startCh" type="number" min="1" class="glass-input flex-1" />
        </div>
        <div class="flex items-center gap-2">
          <label class="text-sm shrink-0" style="color: var(--color-system-gray)">结束章:</label>
          <input v-model.number="endCh" type="number" min="1" class="glass-input flex-1" />
        </div>
        <div class="flex items-center gap-2">
          <label class="text-sm shrink-0" style="color: var(--color-system-gray)">批次大小:</label>
          <input v-model.number="batchSize" type="number" min="5" class="glass-input flex-1" />
        </div>
        <div class="flex items-center gap-2">
          <label class="text-sm shrink-0" style="color: var(--color-system-gray)">并发数:</label>
          <input v-model.number="concurrency" type="number" min="1" max="4" class="glass-input flex-1" />
        </div>
      </div>

      <div class="flex gap-2">
        <button v-if="!summaryStatus?.running" @click="startSummary" :disabled="!bookId" class="glass-button glass-button-primary">开始总结</button>
        <button v-else @click="stopSummary" class="glass-button glass-button-danger">停止</button>
        <button @click="loadReport" :disabled="!bookId" class="glass-button">加载报告</button>
      </div>

      <!-- 细化进度条：对齐旧版日志「最终总结」部分的 4 阶段粒度 -->
      <div v-if="showProgress" class="summary-progress">
        <div class="step-row">
          <div v-for="(ph, i) in SUMMARY_PHASES" :key="ph.key" class="step" :class="stepClass(i)">
            <span class="step-dot">{{ stepClass(i) === 'done' ? '✓' : (i + 1) }}</span>
            <span class="step-label">{{ ph.label }}</span>
          </div>
        </div>

        <div class="bar"><div class="bar-fill" :style="{ width: overallPct + '%' }"></div></div>

        <div class="progress-meta">
          <span class="pm-phase">{{ phaseLabel }}</span>
          <span v-if="summaryStatus?.phase === 'batch'" class="pm-sub">
            卷 {{ summaryStatus.batches_done }}/{{ summaryStatus.total_batches }} · {{ batchPct }}%
          </span>
          <span v-else-if="summaryStatus?.phase === 'complete'" class="pm-sub pm-ok">✓ 全书脉络报告已生成</span>
          <span v-else-if="summaryStatus?.phase === 'failed'" class="pm-sub pm-warn">✗ 总结失败</span>
          <span v-else-if="summaryStatus?.phase === 'stopped'" class="pm-sub pm-warn">■ 已停止</span>
          <span class="pm-time">⏱ {{ elapsedText }}</span>
        </div>
        <p v-if="summaryStatus?.error" class="pm-error">错误: {{ summaryStatus.error }}</p>
        <p v-else-if="summaryStatus?.phase === 'complete'" class="pm-done-note">
          共 {{ summaryStatus.total_batches }} 批 · 总耗时 {{ elapsedText }}
        </p>
      </div>

      <div v-if="report" class="glass-card p-5 md-content" v-html="reportHtml"></div>
      <div v-if="!report" class="text-center py-8 text-sm" style="color: var(--text-tertiary)">
        尚未加载报告。点击「加载报告」查看 final_summary_report.md
      </div>
    </section>

    <!-- 区块二：数据聚合 -->
    <section class="glass-card p-4 space-y-3">
      <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">数据聚合</h3>
      <div class="flex gap-2 flex-wrap">
        <button @click="runAggregate" :disabled="aggBusy || !bookId" class="glass-button glass-button-primary">开始聚合</button>
        <button @click="loadAggFiles" :disabled="!bookId" class="glass-button">刷新文件</button>
        <button @click="exportExcel" :disabled="!bookId" class="glass-button">导出Excel</button>
      </div>
      <p v-if="aggError" class="glass-tinted-red px-3 py-2 rounded text-sm" style="color: var(--color-system-red)">{{ aggError }}</p>
      <div v-if="aggFiles.length" class="flex gap-2 flex-wrap">
        <button v-for="f in aggFiles" :key="f.name" @click="viewAggFile(f.name)" class="glass-button" style="font-size: 12px">{{ f.name }} ({{ f.size_kb }}KB)</button>
      </div>
      <pre v-if="aggContent" class="bg-gray-900 text-green-400 p-4 rounded text-xs overflow-auto">{{ aggIsJson ? JSON.stringify(aggContent, null, 2) : aggContent }}</pre>
    </section>
  </div>
</template>

<style scoped>
/* ===== 细化进度条（对齐旧版日志 4 阶段粒度）===== */
.summary-progress {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  background: var(--glass-frost);
  border: 1px solid var(--glass-rim-color);
  backdrop-filter: blur(10px) saturate(160%);
  -webkit-backdrop-filter: blur(10px) saturate(160%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4), 0 1px 3px rgba(0, 0, 0, 0.06);
}

.step-row {
  display: flex;
  gap: 8px;
}
.step {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 6px;
  border-radius: 10px;
  font-size: 12px;
  border: 1px solid var(--glass-rim-color);
  background: rgba(0, 0, 0, 0.03);
  color: var(--text-tertiary);
  transition: all 0.3s ease;
}
.step-dot {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  flex-shrink: 0;
  background: rgba(0, 0, 0, 0.06);
  color: var(--text-secondary);
}
.step-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.step.active {
  background: rgba(10, 132, 255, 0.12);
  border-color: var(--color-system-blue);
  color: var(--text-primary);
}
.step.active .step-dot {
  background: var(--color-system-blue);
  color: #fff;
}
.step.done {
  color: var(--color-system-green);
  border-color: rgba(48, 209, 88, 0.5);
}
.step.done .step-dot {
  background: var(--color-system-green);
  color: #fff;
}

.bar {
  height: 8px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.08);
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #0a84ff, #5ac8fa);
  transition: width 0.45s ease;
}

.progress-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--text-secondary);
}
.pm-phase {
  font-weight: 600;
  color: var(--text-primary);
}
.pm-sub {
  color: var(--color-system-gray);
}
.pm-ok {
  color: var(--color-system-green);
}
.pm-warn {
  color: var(--color-system-red);
}
.pm-time {
  margin-left: auto;
  color: var(--text-tertiary);
}
.pm-error {
  font-size: 12px;
  color: var(--color-system-red);
}
.pm-done-note {
  font-size: 12px;
  color: var(--color-system-gray);
}
</style>
