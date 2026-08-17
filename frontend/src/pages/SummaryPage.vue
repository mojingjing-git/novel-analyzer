<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import BookSelector from '../components/BookSelector.vue'
import LogConsole, { type LogEntry } from '../components/LogConsole.vue'
import { api, type SummaryStatus } from '../api/client'
import { renderMarkdown } from '../utils/markdown'
import { useLogStore } from '../composables/useLogStore'

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
  try { const res = await api.getAggregateFile(bookId.value, name); aggContent.value = res.content; aggIsJson.value = res.is_json } catch (e) { aggError.value = (e as Error).message }
}

const aggNotice = ref('')
async function exportExcel() {
  aggError.value = ''
  aggNotice.value = ''
  try { await api.exportAggregateExcel(bookId.value); aggNotice.value = '导出成功' } catch (e) { aggError.value = (e as Error).message }
}

// ===== 区块二：最终总结 =====
const startCh = ref(1)
const endCh = ref(99999)
// 0802 spec：批次/并发参数持久化到后端 config.json（取代 localStorage）
const batchSize = ref(30)
const concurrency = ref(2)
// 总结专用模型/思考覆盖：空=跟随全局设置
const summaryModel = ref('')
const summaryThinking = ref('{}')
const modelOptions = ref<string[]>([])
let paramsLoaded = false
let persistTimer: ReturnType<typeof setTimeout> | null = null
let persistVersion = 0
const paramSaveError = ref('')
watch(batchSize, () => { if (paramsLoaded) schedulePersist() })
watch(concurrency, () => { if (paramsLoaded) schedulePersist() })
watch(summaryModel, () => { if (paramsLoaded) schedulePersist() })
watch(summaryThinking, () => { if (paramsLoaded) schedulePersist() })

// 防抖 + 版本号串行化：连续修改只触发一次写入；写入时重新读服务器最新配置再全量回写，
// 避免多个并发 PUT 基于旧快照互相覆盖（旧实现的竞态：快速改两个参数会丢其中一个）。
function schedulePersist() {
  persistVersion++
  const myVersion = persistVersion
  if (persistTimer) clearTimeout(persistTimer)
  persistTimer = setTimeout(async () => {
    persistTimer = null
    await persistSummaryParams()
    if (myVersion !== persistVersion) schedulePersist() // 写入期间又有改动，补一轮
  }, 600)
}

async function persistSummaryParams() {
  try {
    paramSaveError.value = ''
    // 注意：PUT /api/settings 是全量替换（缺失字段回退默认值），
    // 必须基于完整配置修改后整体写回，否则会重置 api_key 等其它配置。
    const cfg = await api.getSettings()
    cfg.analysis.summary_batch_size = batchSize.value
    cfg.analysis.summary_concurrency = concurrency.value
    cfg.api.summary_model = summaryModel.value
    cfg.api.summary_thinking_mode = jsonStringToDict(summaryThinking.value)
    await api.putSettings(cfg)
  } catch (e) {
    // 分析运行中 PUT 会返回 409：显式提示，避免用户以为改成功
    paramSaveError.value = '保存总结参数失败: ' + (e as Error).message
  }
}

// 思考控制选项（JSON 字符串 ↔ dict，按参数名区分，不按厂商模型命名）
const THINKING_OPTIONS = [
  { value: '{}', label: '自动（跟随全局设置）' },
  { value: '{"thinking":{"type":"disabled"}}', label: 'thinking: disabled' },
  { value: '{"reasoning_effort":"none"}', label: 'reasoning_effort: none' },
  { value: '{"enable_thinking":false}', label: 'enable_thinking: false' },
]

function dictToJsonString(v: Record<string, unknown> | undefined): string {
  if (!v || Object.keys(v).length === 0) return '{}'
  return JSON.stringify(v)
}
function jsonStringToDict(s: string): Record<string, unknown> {
  try { return s === '{}' ? {} : JSON.parse(s) } catch { return {} }
}

async function loadSummaryParams() {
  try {
    const cfg = await api.getSettings()
    batchSize.value = cfg.analysis.summary_batch_size ?? 30
    concurrency.value = cfg.analysis.summary_concurrency ?? 2
    summaryModel.value = cfg.api.summary_model ?? ''
    summaryThinking.value = dictToJsonString(cfg.api.summary_thinking_mode)
    // 当前模型不在列表时补一项（支持列表接口没返回的自定义模型）
    if (summaryModel.value && !modelOptions.value.includes(summaryModel.value)) {
      modelOptions.value.push(summaryModel.value)
    }
  } catch (e) { console.error('读取总结参数失败:', e) }
  paramsLoaded = true
}
async function loadModelOptions() {
  try {
    const res = await api.getModels()
    modelOptions.value = res.models ?? []
    if (summaryModel.value && !modelOptions.value.includes(summaryModel.value)) {
      modelOptions.value.push(summaryModel.value)
    }
  } catch (e) { console.error('获取模型列表失败:', e) }
}
const summaryStatus = ref<SummaryStatus | null>(null)
const report = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

// ===== 总结日志抽屉（业务+技术日志，按后端 category="summary" 过滤）=====
const { logs: allLogs, removeByCategory } = useLogStore()
const summaryLogs = computed(() =>
  (allLogs.value as (LogEntry & { category?: string })[]).filter(
    (l) => l.category === 'summary' && l.kind !== 'debug' && l.source !== 'block',
  ),
)
const drawerOpen = ref(false)
// 点击开始总结后自动展开；阶段完成（报告输出）后自动收起；可手动切换
watch(summaryStatus, (s) => {
  if (s?.phase === 'complete') drawerOpen.value = false
})

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

const SUMMARY_TOKEN_LABELS: Record<string, string> = {
  summary: '分卷摘要', reconciliation: '伏笔调和', recheck: '全书复检', style: '风格分析', final: '最终报告',
}
const summaryTokenText = computed(() => {
  const stats = summaryStatus.value?.token_stats
  if (!stats) return ''
  const parts = Object.entries(SUMMARY_TOKEN_LABELS)
    .map(([k, label]) => {
      const v = stats[k]
      return v && (v.input_tokens || v.output_tokens) ? `${label} 入${v.input_tokens} 出${v.output_tokens}` : ''
    })
    .filter(Boolean)
  return parts.length ? `本次总结 token：${parts.join(' · ')}` : ''
})

const showProgress = computed(() => {
  const s = summaryStatus.value
  if (!s) return false
  if (s.running) return true
  return ['complete', 'failed', 'stopped'].includes(s.phase)
})

const summaryError = ref('')
async function startSummary() {
  summaryError.value = ''
  try {
    await api.startSummary(bookId.value, startCh.value, endCh.value, batchSize.value, concurrency.value)
    drawerOpen.value = true // 开始总结 → 自动展开日志抽屉
  } catch (e) { summaryError.value = (e as Error).message }
}
async function stopSummary() { try { await api.stopSummary() } catch (e) { summaryError.value = (e as Error).message } }
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

onMounted(() => { loadSummaryParams(); loadModelOptions(); refreshSummaryStatus(); loadAggFiles(); loadReport(); pollTimer = setInterval(refreshSummaryStatus, 2000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<template>
  <div class="space-y-6">
    <div>
      <h2 class="section-title">聚合与总结</h2>
      <p class="section-subtitle">最终总结生成全书报告，数据聚合生成结构化结果（可二选一使用）</p>
    </div>

    <BookSelector v-model="bookId" />

    <!-- 区块一：最终总结（更常用，置顶） -->
    <section class="glass-card p-4 space-y-3">
      <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--win-stroke); letter-spacing: -0.01em">最终总结</h3>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="flex items-center gap-2">
          <label class="text-sm shrink-0" style="color: var(--win-text-secondary)">起始章:</label>
          <input v-model.number="startCh" type="number" min="1" class="glass-input flex-1" />
        </div>
        <div class="flex items-center gap-2">
          <label class="text-sm shrink-0" style="color: var(--win-text-secondary)">结束章:</label>
          <input v-model.number="endCh" type="number" min="1" class="glass-input flex-1" />
        </div>
        <div class="flex items-center gap-2">
          <label class="text-sm shrink-0" style="color: var(--win-text-secondary)">批次大小:</label>
          <input v-model.number="batchSize" type="number" min="5" class="glass-input flex-1" />
        </div>
        <div class="flex items-center gap-2">
          <label class="text-sm shrink-0" style="color: var(--win-text-secondary)">并发数:</label>
          <input v-model.number="concurrency" type="number" min="1" max="20" class="glass-input flex-1" />
        </div>
      </div>

      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="flex items-center gap-2">
          <label class="text-sm shrink-0" style="color: var(--win-text-secondary)" title="最终总结专用模型，复用同一 base_url/api_key；空=跟随全局模型（重型任务可切 MiniMax-M3 等）">总结模型:</label>
          <select v-model="summaryModel" class="glass-input flex-1">
            <option value="">跟随全局设置</option>
            <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <label class="text-sm shrink-0" style="color: var(--win-text-secondary)" title="总结专用思考控制，空=跟随全局；M3/mimo/GLM 用 thinking 参数，DeepSeek/Qwen 用 enable_thinking（M2.x 关不掉思考）">思考模式:</label>
          <select v-model="summaryThinking" class="glass-input flex-1">
            <option v-for="o in THINKING_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </div>
      </div>
      <p v-if="paramSaveError" class="glass-tinted-red px-3 py-2 rounded text-sm">{{ paramSaveError }}</p>

      <div class="flex gap-2">
        <button v-if="!summaryStatus?.running" @click="startSummary" :disabled="!bookId" class="glass-button glass-button-primary">开始总结</button>
        <button v-else @click="stopSummary" class="glass-button glass-button-danger">停止</button>
        <button @click="loadReport" :disabled="!bookId" class="glass-button">加载报告</button>
      </div>

      <p v-if="summaryError" class="glass-tinted-red px-3 py-2 rounded text-sm">{{ summaryError }}</p>

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

      <div v-if="summaryTokenText" class="text-xs" style="color: var(--win-text-secondary)">{{ summaryTokenText }}</div>

      <!-- 总结日志抽屉：阶段提示 + 技术明细（category=summary），报告区上方 -->
      <div class="glass-card">
        <div class="flex items-center gap-2 px-3 py-2 cursor-pointer select-none" @click="drawerOpen = !drawerOpen">
          <Icon name="arrow_down" :size="13" :style="{ transform: drawerOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }" />
          <span class="text-sm font-medium" style="color: var(--win-text-primary)">总结日志</span>
          <span class="text-xs" style="color: var(--win-text-disabled)">{{ summaryLogs.length }} 条</span>
          <span class="flex-1"></span>
          <button
            v-if="summaryLogs.length"
            @click.stop="removeByCategory('summary')"
            class="glass-button btn-sm"
            style="font-size: 11px"
          >清空</button>
        </div>
        <div v-if="drawerOpen" class="p-2">
          <LogConsole :logs="summaryLogs" />
        </div>
      </div>

      <div v-if="report" class="glass-card p-5 md-content" v-html="reportHtml"></div>
      <div v-if="!report" class="text-center py-8 text-sm" style="color: var(--win-text-disabled)">
        尚未加载报告。点击「加载报告」查看 final_summary_report.md
      </div>
    </section>

    <!-- 区块二：数据聚合 -->
    <section class="glass-card p-4 space-y-3">
      <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--win-stroke); letter-spacing: -0.01em">数据聚合</h3>
      <div class="flex gap-2 flex-wrap">
        <button @click="runAggregate" :disabled="aggBusy || !bookId" class="glass-button glass-button-primary">开始聚合</button>
        <button @click="loadAggFiles" :disabled="!bookId" class="glass-button">刷新文件</button>
        <button @click="exportExcel" :disabled="!bookId" class="glass-button">导出Excel</button>
      </div>
      <p v-if="aggError" class="glass-tinted-red px-3 py-2 rounded text-sm">{{ aggError }}</p>
      <p v-if="aggNotice" class="glass-tinted-green px-3 py-2 rounded text-sm">{{ aggNotice }}</p>
      <div v-if="aggFiles.length" class="flex gap-2 flex-wrap">
        <button v-for="f in aggFiles" :key="f.name" @click="viewAggFile(f.name)" class="glass-button" style="font-size: 12px">{{ f.name }} ({{ f.size_kb }}KB)</button>
      </div>
      <pre v-if="aggContent" class="code-surface code-green p-4 overflow-auto max-h-96">{{ aggIsJson ? JSON.stringify(aggContent, null, 2) : aggContent }}</pre>
    </section>
  </div>
</template>

<style scoped>
/* ===== 细化进度条（对齐旧版日志 4 阶段粒度）===== */
.summary-progress {
  display: flex;
  flex-direction: column;
  gap: 12px;
  /* 16px 16px 对齐 4 倍数（原 14px 16px 不合规） */
  padding: 16px;
  border-radius: var(--win-radius-container);
  background: var(--win-layer);
  border: 1px solid var(--win-stroke);
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
  gap: 8px;
  height: 40px;
  padding: 0 8px;
  border-radius: var(--win-radius-control);
  font-size: 12px;
  border: 1px solid var(--win-stroke);
  background: var(--win-control-alt);
  color: var(--win-text-disabled);
  transition: background var(--win-duration-fast) var(--win-ease), border-color var(--win-duration-fast) var(--win-ease), color var(--win-duration-fast) var(--win-ease);
}
.step-dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
  background: var(--win-control-hover);
  color: var(--win-text-secondary);
}
.step-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.step.active {
  background: var(--win-accent-soft);
  border-color: var(--win-accent);
  color: var(--win-text-primary);
}
.step.active .step-dot {
  background: var(--win-accent);
  color: #fff;
}
.step.done {
  background: var(--win-success-bg);
  border-color: var(--win-success);
  color: var(--win-success);
}
.step.done .step-dot {
  background: var(--win-success);
  color: #fff;
}

.bar {
  height: 4px;
  border-radius: var(--win-radius-pill);
  background: var(--win-control-alt);
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: var(--win-radius-pill);
  background: var(--win-accent);
  transition: width 0.45s var(--win-ease);
}

.progress-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--win-text-secondary);
}
.pm-phase {
  font-weight: 600;
  color: var(--win-text-primary);
}
.pm-sub {
  color: var(--win-text-secondary);
}
.pm-ok {
  color: var(--win-success);
}
.pm-warn {
  color: var(--win-danger);
}
.pm-time {
  margin-left: auto;
  color: var(--win-text-disabled);
}
.pm-error {
  font-size: 12px;
  color: var(--win-danger);
}
.pm-done-note {
  font-size: 12px;
  color: var(--win-text-secondary);
}
</style>
