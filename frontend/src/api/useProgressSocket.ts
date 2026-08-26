// WebSocket 进度推送 composable（单例模式）
// 全应用共享一个 WebSocket 连接，避免页面切换时断开重连丢失消息
import { ref, onUnmounted, type Ref } from 'vue'

export interface ProgressMessage {
  type: 'log' | 'progress' | 'block_done' | 'state_change' | 'token_stats' | 'ping' | 'summary_progress' | 'location_normalization_progress' | 'block_start' | 'discovery'
  payload: Record<string, unknown>
}

export interface LogEntry {
  id: number
  text: string
  kind: 'info' | 'warn' | 'error' | 'state' | 'debug'
  category?: string  // analysis / style / splitter / aggregate / summary ...
  source?: string    // python=Python logging转发的技术日志 / business=业务消息 / block=逐章完成事件 / local=前端操作 / event=状态事件
}

// ============ 单例状态 ============
let sharedWs: WebSocket | null = null
const sharedConnected = ref(false)
const allSubscribers = new Set<(msg: ProgressMessage) => void>()
let retry = 0
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let isInitialized = false
let manualClose = false  // 主动关闭标记：避免 closeProgressSocket 后 onclose 仍触发重连产生孤儿 WS

function connect() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${proto}//${window.location.host}/ws/progress`
  try {
    sharedWs = new WebSocket(url)
  } catch (e) {
    console.error('[WS] 创建连接失败:', e)
    scheduleReconnect()
    return
  }

  sharedWs.onopen = () => {
    sharedConnected.value = true
    retry = 0
    console.log('[WS] 已连接')
  }

  sharedWs.onmessage = (event) => {
    try {
      const msg: ProgressMessage = JSON.parse(event.data)
      if (msg.type !== 'ping') {
        // 分发给所有订阅者
        for (const sub of allSubscribers) {
          try {
            sub(msg)
          } catch (e) {
            console.error('[WS] 订阅者处理失败:', e)
          }
        }
      }
    } catch (e) {
      console.error('[WS] 解析消息失败:', e)
    }
  }

  sharedWs.onclose = () => {
    sharedConnected.value = false
    if (manualClose) {
      // 主动关闭（closeProgressSocket）后不再重连，否则会生成无人能清理的孤儿 WS
      manualClose = false
      console.log('[WS] 连接已主动关闭')
      return
    }
    console.log('[WS] 连接断开，重试中...')
    scheduleReconnect()
  }

  sharedWs.onerror = () => {
    sharedWs?.close()
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return  // 已有重连任务
  const delay = Math.min(1000 * Math.pow(2, retry), 10000)
  retry++
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connect()
  }, delay)
}

/** 初始化全局 WS（在 App 启动时调用一次） */
export function initProgressSocket() {
  if (!isInitialized) {
    isInitialized = true
    connect()
  }
}

export function useProgressSocket(onMessage: (msg: ProgressMessage) => void): { connected: Ref<boolean> } {
  // 首次调用时初始化 WS
  if (!isInitialized) {
    initProgressSocket()
  }
  // 注册订阅者（页面卸载时移除）
  allSubscribers.add(onMessage)
  onUnmounted(() => {
    allSubscribers.delete(onMessage)
  })
  return { connected: sharedConnected }
}

/** 关闭 WS（测试或卸载时使用） */
export function closeProgressSocket() {
  manualClose = true
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (sharedWs) {
    sharedWs.close()
    sharedWs = null
  }
  sharedConnected.value = false
  isInitialized = false
}