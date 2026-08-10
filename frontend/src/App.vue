<script setup lang="ts">
// 根组件 — 启动全局日志收集器（WS 单例 + 全局日志存储）
import { useProgressSocket, type ProgressMessage } from './api/useProgressSocket'
import { useLogStore } from './composables/useLogStore'

// 在 setup 阶段订阅（不能在 onMounted 内调用 useProgressSocket，否则其内部 onUnmounted 注册失效并产生警告）。
// useProgressSocket 内部已保证 WS 仅初始化一次（单例）。
useProgressSocket((msg: ProgressMessage) => {
  const store = useLogStore()
  if (msg.type === 'log') {
    const level = (msg.payload.level as string) || 'info'
    const text = msg.payload.text as string
    const category = (msg.payload.category as string) || 'analysis'
    // source：后端已标记 python/business；旧后端未标记时按文本兜底识别技术日志，
    // 保证"简化日志"面板不混入 Python 内部日志
    const payloadSource = msg.payload.source as string | undefined
    const source = payloadSource
      ?? (/\s\[(INFO|WARN|ERROR|DEBUG)\]\s/.test(text) ? 'python' : 'business')
    store.add(text, level as 'info' | 'warn' | 'error' | 'state', category, source)
  } else if (msg.type === 'state_change') {
    const state = msg.payload.state as string
    const detail = (msg.payload.detail as string) ?? ''
    store.add(`状态: ${state} - ${detail}`, 'state', 'system', 'event')
  } else if (msg.type === 'block_done') {
    const ch = msg.payload.chapter as number
    const ok = msg.payload.ok
    const elapsed = Number(msg.payload.elapsed) || 0
    if (ok) {
      store.add(`第${ch}章分析完成 (${elapsed.toFixed(1)}s)`, 'info', 'analysis', 'block')
    } else {
      store.add(`第${ch}章分析失败`, 'error', 'analysis', 'block')
    }
  }
})
</script>

<template>
  <router-view />
</template>