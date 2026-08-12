// 全局日志存储（模块级单例）
// 特性：
// - 跨页面切换保留日志
// - localStorage 持久化（跨会话）
// - 自动去重（log_id 滚动递增）
// - 容量限制（最多 2000 条，超出自动丢弃旧的）
import { ref, watch, type Ref } from 'vue'
import type { LogEntry } from '../api/useProgressSocket'

const STORAGE_KEY = 'novel-analyzer-logs'
const MAX_LOGS = 2000

// 旧数据迁移：早期版本没有 source 字段，Python logging 转发的日志格式为
// "2026-08-07 10:00:00 [INFO] backend.core.pipeline: ..."，据此把旧技术日志
// 标记为 python，让"简化日志"面板能正确过滤掉历史会话的技术噪音。
const PYTHON_LOG_RE = /\[(INFO|WARN|ERROR|DEBUG)\]/

function migrateSource(entry: LogEntry): LogEntry {
  if (!entry.source && PYTHON_LOG_RE.test(entry.text || '')) {
    return { ...entry, source: 'python' }
  }
  return entry
}

function loadFromStorage(): LogEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        // 过滤掉无效条目 + 旧数据来源迁移
        return parsed
          .filter(x => x && typeof x.text === 'string')
          .map(migrateSource)
      }
    }
  } catch (e) {
    console.warn('加载日志失败:', e)
  }
  return []
}

function saveToStorage(logs: LogEntry[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(logs))
  } catch (e) {
    // localStorage 可能满了或被禁用，忽略
  }
}

// 模块级单例：跨组件、跨页面、跨会话
const logs: Ref<LogEntry[]> = ref<LogEntry[]>(loadFromStorage())
// 加载即裁剪：localStorage 可能存有超过上限的旧日志（add 才裁剪会漏掉加载路径）
if (logs.value.length > MAX_LOGS) {
  logs.value.splice(0, logs.value.length - MAX_LOGS)
}

// 下一个 log id 必须大于已加载的最大 id，避免重复
let nextId = (logs.value.reduce((m, x) => Math.max(m, x.id), 0)) + 1

// 节流持久化：避免高频 log 写入阻塞 UI
let saveTimer: ReturnType<typeof setTimeout> | null = null
watch(
  logs,
  () => {
    if (saveTimer) return  // 已有待执行保存
    saveTimer = setTimeout(() => {
      saveTimer = null
      saveToStorage(logs.value)
    }, 500)
  },
  { deep: true }
)

export function useLogStore() {
  /** 追加一条日志 */
  function add(text: string, kind: LogEntry['kind'] = 'info', category: string = 'analysis', source?: string) {
    if (!text) return
    logs.value.push({ id: nextId++, text, kind, category, source } as LogEntry & { category?: string })
    if (logs.value.length > MAX_LOGS) {
      // 保留最新的 MAX_LOGS 条
      logs.value.splice(0, logs.value.length - MAX_LOGS)
    }
  }

  /** 批量追加（用于从 localStorage 恢复时的初始化） */
  function addBatch(items: LogEntry[]) {
    logs.value.push(...items)
  }

  /** 清空所有日志 */
  function clear() {
    logs.value = []
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {}
  }

  /** 按 category 过滤 */
  function filter(category?: string): LogEntry[] {
    if (!category) return logs.value
    return logs.value.filter(x => (x as LogEntry & { category?: string }).category === category)
  }

  return { logs, add, addBatch, clear, filter }
}