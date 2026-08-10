<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api, type WorkspaceNovel, type WorkspaceArchive } from '../api/client'
import ConfirmDialog from '../components/ConfirmDialog.vue'

interface NovelRow {
  name: string
  chapters: number
  size: number
  modified: number
}
interface ArchiveRow {
  name: string
  size: number
  modified: number
}

const novels = ref<NovelRow[]>([])
const archives = ref<ArchiveRow[]>([])
const loading = ref(false)
const error = ref('')

// 统一确认弹窗状态（替代浏览器原生 confirm()）
const confirmState = ref<{ title: string; message: string; confirmText: string; danger: boolean; action: () => Promise<void> } | null>(null)
function askConfirm(title: string, message: string, confirmText: string, danger: boolean, action: () => Promise<void>) {
  confirmState.value = { title, message, confirmText, danger, action }
}
async function runConfirmed() {
  const s = confirmState.value
  confirmState.value = null
  if (!s) return
  try {
    await s.action()
    await load()
  } catch (e) {
    error.value = (e as Error).message || String(e)
  }
}

function fmtSize(b: number): string {
  if (!b || b < 0) return '0 B'
  if (b < 1024) return b + ' B'
  const kb = b / 1024
  if (kb < 1024) return kb.toFixed(0) + ' KB'
  const mb = kb / 1024
  if (mb < 1024) return mb.toFixed(1) + ' MB'
  return (mb / 1024).toFixed(2) + ' GB'
}
function fmtTime(ts: number): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [n, a] = await Promise.all([api.workspaceNovels(), api.workspaceArchives()])
    novels.value = n.novels.map((x: WorkspaceNovel) => ({
      name: x.name,
      chapters: x.blocks_count,
      size: x.dir_size,
      modified: x.dir_mtime,
    }))
    archives.value = a.archives.map((x: WorkspaceArchive) => ({
      name: x.name,
      size: x.total_size,
      modified: x.dir_mtime,
    }))
  } catch (e) {
    error.value = (e as Error).message || String(e)
  } finally {
    loading.value = false
  }
}
function archive(name: string) {
  askConfirm('归档小说', `归档《${name}》?`, '归档', false, async () => { await api.archiveNovel(name) })
}
function archiveAll() {
  askConfirm('批量归档', '批量归档所有小说?', '全部归档', false, async () => { await api.archiveAll() })
}
function delArchive(name: string) {
  askConfirm('删除归档', `删除归档《${name}》? 此操作不可恢复`, '删除', true, async () => { await api.deleteArchive(name) })
}

onMounted(load)
</script>

<template>
  <div class="space-y-4 max-w-4xl p-4">
    <div>
      <h2 class="section-title">工作区管理</h2>
      <p class="section-subtitle">管理 workspace/ 中的小说与 分析结果/ 归档</p>
    </div>

    <div v-if="error" class="glass-tinted-red px-4 py-2 rounded-ios-md text-sm">{{ error }}</div>

    <!-- 小说列表 -->
    <div class="glass-card p-4 space-y-3">
      <div class="flex items-center justify-between pb-2" style="border-bottom: 1px solid var(--glass-border-subtle)">
        <h3 class="font-semibold">workspace/ 小说</h3>
        <button
          @click="archiveAll"
          :disabled="loading || novels.length === 0"
          class="glass-button"
        >
          批量归档
        </button>
      </div>

      <div v-if="loading" class="text-sm py-4" style="color: var(--text-tertiary)">加载中…</div>
      <div
        v-else-if="novels.length === 0"
        class="text-sm py-4"
        style="color: var(--text-tertiary)"
      >
        暂无小说。请先在「小说切分」切分，或把小说放入 workspace/ 目录。
      </div>
      <div v-else class="space-y-1">
        <div
          v-for="n in novels"
          :key="n.name"
          class="flex items-center justify-between py-2 px-2 rounded"
          style="border-bottom: 1px solid var(--glass-border-subtle)"
        >
          <div class="min-w-0">
            <div class="truncate font-medium">{{ n.name }}</div>
            <div class="text-xs" style="color: var(--color-system-gray)">
              {{ n.chapters }} 章 · {{ fmtSize(n.size) }} · 修改于 {{ fmtTime(n.modified) }}
            </div>
          </div>
          <button @click="archive(n.name)" class="glass-button glass-button-primary shrink-0">归档</button>
        </div>
      </div>
    </div>

    <!-- 归档列表 -->
    <div class="glass-card p-4 space-y-3">
      <h3 class="font-semibold pb-2" style="border-bottom: 1px solid var(--glass-border-subtle)">分析结果/ 归档</h3>
      <div v-if="loading" class="text-sm py-4" style="color: var(--text-tertiary)">加载中…</div>
      <div v-else-if="archives.length === 0" class="text-sm py-4" style="color: var(--text-tertiary)">暂无归档。</div>
      <div v-else class="space-y-1">
        <div
          v-for="a in archives"
          :key="a.name"
          class="flex items-center justify-between py-2 px-2 rounded"
          style="border-bottom: 1px solid var(--glass-border-subtle)"
        >
          <div class="min-w-0">
            <div class="truncate font-medium">{{ a.name }}</div>
            <div class="text-xs" style="color: var(--color-system-gray)">
              {{ fmtSize(a.size) }} · 修改于 {{ fmtTime(a.modified) }}
            </div>
          </div>
          <button @click="delArchive(a.name)" class="glass-button glass-button-danger shrink-0">删除</button>
        </div>
      </div>
    </div>

    <!-- 统一确认弹窗（玻璃材质） -->
    <ConfirmDialog
      v-if="confirmState"
      :title="confirmState.title"
      :message="confirmState.message"
      :confirm-text="confirmState.confirmText"
      :danger="confirmState.danger"
      @confirm="runConfirmed"
      @cancel="confirmState = null"
    />
  </div>
</template>
