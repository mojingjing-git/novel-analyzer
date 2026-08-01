<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api, type AppConfigDto } from '../api/client'
import LogConsole from '../components/LogConsole.vue'
import { useLogStore } from '../composables/useLogStore'

const config = ref<AppConfigDto | null>(null)
const presets = ref<Record<string, { base_url: string; api_key: string; model: string }>>({})
const models = ref<string[]>([])
const saving = ref(false)
const saved = ref(false)

async function load() {
  try {
    config.value = await api.getSettings()
    presets.value = (await api.getPresets()).presets
  } catch (e) { console.error(e) }
}

async function loadModels() {
  // 用表单当前（未保存）的 base_url/api_key 查模型：填完即可获取，无需先保存；
  // 空字段不传，由后端回退到已保存配置
  const a = config.value?.api
  const body: { base_url?: string; api_key?: string } = {}
  if (a?.base_url?.trim()) body.base_url = a.base_url.trim()
  if (a?.api_key?.trim()) body.api_key = a.api_key.trim()
  try { const res = await api.previewModels(body); models.value = res.models }
  catch (e) { alert('获取模型失败: ' + (e as Error).message) }
}

// 自定义模型下拉：点击即展开全部，可输入筛选，也允许手填自定义模型
const showModelList = ref(false)
const modelFilter = ref('')
const modelBoxRef = ref<HTMLElement | null>(null)

const filteredModels = computed(() => {
  const f = modelFilter.value.trim().toLowerCase()
  if (!f) return models.value
  return models.value.filter((m: string) => m.toLowerCase().includes(f))
})

async function openModelList() {
  showModelList.value = true
  if (models.value.length === 0) {
    await loadModels()
  }
}

function pickModel(m: string) {
  if (config.value) config.value.api.model = m
  modelFilter.value = ''
  showModelList.value = false
}

function onModelBoxBlur() {
  // 延迟收起，避免点击选项时先触发 blur 把列表收掉
  setTimeout(() => { showModelList.value = false }, 120)
}

function onDocClick(e: MouseEvent) {
  if (modelBoxRef.value && !modelBoxRef.value.contains(e.target as Node)) {
    showModelList.value = false
  }
}
onMounted(() => {
  document.addEventListener('click', onDocClick)
})
onUnmounted(() => {
  // AppLayout 按 route.path 强制重挂载，必须移除监听，否则每次访问设置页都泄漏一个全局监听器
  document.removeEventListener('click', onDocClick)
})

function onPreset(name: string) {
  const p = presets.value[name]
  if (p && config.value) {
    config.value.api.base_url = p.base_url
    config.value.api.model = p.model
    if (p.api_key) config.value.api.api_key = p.api_key
  }
}

const fieldErrors = ref<Record<string, string>>({})
const saveError = ref('')

async function save() {
  if (!config.value) return
  saving.value = true
  fieldErrors.value = {}
  saveError.value = ''
  try {
    await api.putSettings(config.value)
    saved.value = true
    setTimeout(() => saved.value = false, 2000)
  } catch (e) {
    const err = e as Error & { status?: number; detail?: unknown }
    if (err.status === 422 && Array.isArray(err.detail)) {
      const msgs: string[] = []
      for (const item of err.detail as Array<{ loc?: (string | number)[]; msg?: string }>) {
        const leaf = item.loc && item.loc.length ? String(item.loc[item.loc.length - 1]) : '?'
        const m = item.msg || '校验未通过'
        fieldErrors.value[leaf] = m
        msgs.push(`${leaf}: ${m}`)
      }
      saveError.value = '保存失败（参数校验未通过）：\n' + msgs.join('\n')
    } else {
      saveError.value = '保存失败: ' + err.message
    }
  }
  finally { saving.value = false }
}

// 根据字段名返回校验出错的 class（用于输入框红框高亮）
function errCls(field: string): string {
  return fieldErrors.value[field] ? 'input-invalid' : ''
}

const jsonModes = [
  { value: 'default', label: 'default - 通用' },
  { value: 'qwen', label: 'qwen - Qwen系列' },
  { value: 'deepseek', label: 'deepseek - DeepSeek V4' },
  { value: 'glm47', label: 'glm47 - GLM-4.7' },
]

// 运行日志（完整，含调试信息）—— 与全局日志存储共享同一单例
const { logs: storeLogs, clear: clearLogs } = useLogStore()

const thinkingModes = [
  { value: '{}', label: '自动（默认）' },
  { value: '{"thinking":{"type":"disabled"}}', label: 'mimo/GLM 禁用思考' },
  { value: '{"enable_thinking":false}', label: 'DeepSeek/Qwen 禁用思考' },
]

function getThinkingMode(): string {
  if (!config.value) return '{}'
  const tm = config.value.api.thinking_mode
  if (!tm || Object.keys(tm).length === 0) return '{}'
  return JSON.stringify(tm)
}

function setThinkingMode(val: string) {
  if (!config.value) return
  try { config.value.api.thinking_mode = val === '{}' ? {} : JSON.parse(val) } catch {}
}

onMounted(load)
</script>

<template>
  <div class="space-y-4 max-w-3xl p-4" v-if="config">
    <div>
      <h2 class="section-title">设置</h2>
      <p class="section-subtitle">API、分析、知识库限制等参数</p>
    </div>

    <div class="glass-card p-4 space-y-3">
      <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">API 配置</h3>
      <div class="flex items-center gap-2">
        <label class="w-32 text-sm" style="color: var(--color-system-gray)">API 预设:</label>
        <select @change="onPreset(($event.target as HTMLSelectElement).value)" class="glass-input flex-1">
          <option value="">选择预设...</option>
          <option v-for="(_, name) in presets" :key="name" :value="name">{{ name }}</option>
        </select>
      </div>
      <div class="flex items-center gap-2">
        <label class="w-32 text-sm" style="color: var(--color-system-gray)">Base URL:</label>
        <input v-model="config.api.base_url" :class="errCls('base_url')" class="glass-input flex-1" />
      </div>
      <div class="flex items-center gap-2">
        <label class="w-32 text-sm" style="color: var(--color-system-gray)">API Key:</label>
        <input v-model="config.api.api_key" :class="errCls('api_key')" type="password" class="glass-input flex-1" />
      </div>
      <div class="flex items-center gap-2">
        <label class="w-32 text-sm shrink-0" style="color: var(--color-system-gray)">模型:</label>
        <div class="relative flex-1" ref="modelBoxRef">
          <input
            v-model="config.api.model"
            :class="errCls('model')"
            @focus="openModelList"
            @click="openModelList"
            @input="showModelList = true; modelFilter = config?.api.model || ''"
            @blur="onModelBoxBlur"
            class="glass-input w-full"
            placeholder="点击选择或输入自定义模型名"
          />
          <div
            v-if="showModelList"
            class="absolute z-20 mt-1 w-full max-h-60 overflow-auto rounded-lg border p-1 space-y-0.5"
            style="background: var(--glass-frost); border-color: var(--glass-rim-color); backdrop-filter: var(--glass-blur); box-shadow: var(--glass-shadow), var(--glass-inner)"
          >
            <div v-if="filteredModels.length === 0" class="px-2 py-1.5 text-xs" style="color: var(--text-tertiary)">
              无匹配模型，以上方输入值为准
            </div>
            <button
              v-for="m in filteredModels"
              :key="m"
              type="button"
              @mousedown.prevent="pickModel(m)"
              class="block w-full text-left px-2 py-1.5 rounded-md text-sm hover:bg-white/20"
              style="color: var(--text-primary)"
            >{{ m }}</button>
          </div>
        </div>
        <button @click="loadModels" class="glass-button" style="padding: 8px 12px; white-space: nowrap">获取模型</button>
      </div>
      <div class="flex items-center gap-2">
        <label class="w-32 text-sm" style="color: var(--color-system-gray)">Max Tokens:</label>
        <input v-model.number="config.api.max_tokens" :class="errCls('max_tokens')" type="number" class="glass-input" style="width: 130px" />
      </div>
      <div class="flex items-center gap-2">
        <label class="w-32 text-sm" style="color: var(--color-system-gray)">Timeout (s):</label>
        <input v-model.number="config.api.timeout" :class="errCls('timeout')" type="number" class="glass-input" style="width: 100px" />
      </div>
      <div class="flex items-center gap-2">
        <label class="w-32 text-sm" style="color: var(--color-system-gray)">JSON 模式:</label>
        <select v-model="config.api.json_mode" class="glass-input flex-1">
          <option v-for="m in jsonModes" :key="m.value" :value="m.value">{{ m.label }}</option>
        </select>
      </div>
      <div class="flex items-center gap-2">
        <label class="w-32 text-sm" style="color: var(--color-system-gray)">思考模式:</label>
        <select :value="getThinkingMode()" @change="setThinkingMode(($event.target as HTMLSelectElement).value)" class="glass-input flex-1">
          <option v-for="m in thinkingModes" :key="m.value" :value="m.value">{{ m.label }}</option>
        </select>
      </div>
    </div>

    <div class="glass-card p-4 space-y-3">
      <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">温度退火 & 重试</h3>
      <div class="grid grid-cols-2 gap-3">
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">初始温度:</label><input v-model.number="config.api.temperature" :class="errCls('temperature')" type="number" step="0.01" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">温度步长:</label><input v-model.number="config.api.temperature_step" :class="errCls('temperature_step')" type="number" step="0.01" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">退火重试:</label><input v-model.number="config.api.temperature_max_retries" :class="errCls('temperature_max_retries')" type="number" min="1" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">退避重试:</label><input v-model.number="config.api.backoff_max_retries" :class="errCls('backoff_max_retries')" type="number" min="0" class="glass-input" style="width: 90px" /></div>
      </div>
    </div>

    <div class="glass-card p-4 space-y-3">
      <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">分析配置</h3>
      <div class="grid grid-cols-2 gap-3">
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">并发数:</label><input v-model.number="config.analysis.concurrency" type="number" min="1" max="8" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">块大小 (章/块):</label><input v-model.number="config.analysis.block_size" type="number" min="1" max="10" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">主线最大字数:</label><input v-model.number="config.analysis.max_arc_length" type="number" class="glass-input" style="width: 110px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">Prompt 主线条数:</label><input v-model.number="config.analysis.max_arcs_in_prompt" type="number" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">Prompt 摘要数:</label><input v-model.number="config.analysis.max_summaries_in_prompt" type="number" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">时间线截断:</label><input v-model.number="config.analysis.timeline_truncate" type="number" class="glass-input" style="width: 110px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">角色状态数:</label><input v-model.number="config.analysis.max_character_states" type="number" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">世界观条数:</label><input v-model.number="config.analysis.max_world_items" type="number" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">伏笔网络条数:</label><input v-model.number="config.analysis.max_foreshadow_entries" type="number" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">伏笔总表上限:</label><input v-model.number="config.analysis.max_foreshadow_catalog" type="number" class="glass-input" style="width: 110px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">分卷摘要字数:</label><input v-model.number="config.analysis.batch_summary_min_words" type="number" class="glass-input" style="width: 110px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">最终报告字数:</label><input v-model.number="config.analysis.final_report_min_words" type="number" class="glass-input" style="width: 110px" /></div>
      </div>
    </div>

    <div class="glass-card p-4 space-y-3">
      <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em">滚动总结参数</h3>
      <div class="grid grid-cols-2 gap-3">
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">触发章数:</label><input v-model.number="config.analysis.rolling_early_chapters" type="number" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">里程碑上限:</label><input v-model.number="config.analysis.rolling_max_milestones" type="number" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">势头上限:</label><input v-model.number="config.analysis.rolling_max_momentum" type="number" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">归档跨度:</label><input v-model.number="config.analysis.rolling_momentum_window" type="number" class="glass-input" style="width: 110px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">归档触发:</label><input v-model.number="config.analysis.rolling_archive_trigger_count" type="number" class="glass-input" style="width: 90px" /></div>
        <div class="flex items-center gap-2"><label class="text-sm flex-1" style="color: var(--color-system-gray)">Checkpoint:</label><input v-model.number="config.analysis.checkpoint_interval" type="number" min="0" max="100" class="glass-input" style="width: 90px" /></div>
      </div>
      <label class="flex items-center gap-2 text-sm cursor-pointer">
        <input v-model="config.analysis.auto_archive" type="checkbox" />
        自动归档
      </label>
    </div>

    <div class="flex gap-3 items-center">
      <button @click="save" :disabled="saving" class="glass-button glass-button-primary" style="padding: 10px 20px">
        {{ saving ? '保存中...' : '保存设置' }}
      </button>
      <Transition name="modal">
        <span v-if="saved" class="glass-badge badge-green" style="font-size: 12px"> 已保存</span>
      </Transition>
    </div>
    <div v-if="saveError" class="glass-card p-3 space-y-1" style="border-color: var(--color-system-red); box-shadow: 0 0 0 1px var(--color-system-red)">
      <p class="text-sm font-medium" style="color: var(--color-system-red)">保存失败</p>
      <pre class="text-xs whitespace-pre-wrap" style="color: var(--color-system-red)">{{ saveError }}</pre>
    </div>

    <!-- 运行日志（完整）：存放真日志，分析队列页只展示简化版 -->
    <div class="glass-card p-4 space-y-3">
      <div class="flex items-center justify-between">
        <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--glass-border-subtle); letter-spacing: -0.01em; flex: 1">运行日志（完整）</h3>
        <button @click="clearLogs" class="glass-button" style="padding: 4px 12px; font-size: 12px; color: var(--color-system-red)">清空日志</button>
      </div>
      <p class="text-xs" style="color: var(--text-tertiary)">
        这是完整的运行日志（含调试信息），跨页面与会话保留。分析队列页面仅显示过滤掉调试信息的简化版。
      </p>
      <LogConsole :logs="storeLogs" class="settings-log" />
    </div>
  </div>
</template>

<style scoped>
.input-invalid {
  border-color: var(--color-system-red) !important;
  box-shadow: 0 0 0 1px var(--color-system-red);
}
</style>