<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import Icon from './Icon.vue'

export interface LogEntry {
  id: number
  text: string
  kind: 'info' | 'warn' | 'error' | 'state' | 'debug'
  source?: string  // python / business / block / local / event（见 simplified 过滤注释）
}

const props = withDefaults(defineProps<{ logs: LogEntry[]; simplified?: boolean }>(), {
  simplified: false,
})
const consoleRef = ref<HTMLElement>()

// 简化模式（分析队列页）：
// 只显示"业务事件流"——队列操作/书级进度/状态变化（source=business/local/event），
// 过滤掉三类噪音：
//   1. debug 级别（kind=debug）
//   2. Python logging 转发的技术日志（source=python，每章 LLM 调用/token 统计等）
//   3. 逐章完成事件（source=block，有进度条展示，无需逐条刷屏）
// 完整模式（设置页）保留全部，含上述内容。
const displayLogs = computed(() =>
  props.simplified
    ? props.logs.filter((l) => l.kind !== 'debug' && l.source !== 'python' && l.source !== 'block')
    : props.logs
)

const kindIcons: Record<string, string> = {
  info: 'info', warn: 'warn', error: 'error', state: 'plus', debug: 'filter',
}
const kindBadge: Record<string, string> = {
  info: 'badge-blue', warn: 'badge-orange', error: 'badge-red',
  state: 'badge-purple', debug: 'badge-gray',
}

watch(
  () => displayLogs.value.length,
  async () => {
    await nextTick()
    if (consoleRef.value) consoleRef.value.scrollTop = consoleRef.value.scrollHeight
  }
)
</script>

<template>
  <div
    ref="consoleRef"
    class="glass-card log-console"
  >
    <div v-if="displayLogs.length === 0" class="log-empty">
      <Icon name="info" :size="14" />
      <span>等待日志...</span>
    </div>
    <div v-for="log in displayLogs" :key="log.id" class="log-row">
      <span class="log-icon" :class="kindBadge[log.kind] || 'badge-gray'">
        <Icon :name="kindIcons[log.kind] || 'info'" :size="9" />
      </span>
      <span class="log-text">{{ log.text }}</span>
    </div>
  </div>
</template>

<style scoped>
.log-console {
  padding: 12px;
  overflow-y: auto;
  font-family: ui-monospace, "SF Mono", "Cascadia Code", monospace;
  font-size: 12px;
  line-height: 1.5;
  max-height: 320px;
  background: rgba(20, 20, 24, 0.55);
}

.log-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgba(255, 255, 255, 0.4);
  padding: 4px 0;
}

.log-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 3px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}

.log-row:last-child { border-bottom: none; }

.log-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  border-radius: 4px;
  margin-top: 1px;
}

.log-text {
  flex: 1;
  word-break: break-all;
  color: rgba(255, 255, 255, 0.9);
}
</style>