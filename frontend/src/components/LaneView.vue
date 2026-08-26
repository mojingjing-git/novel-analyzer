<script setup lang="ts">
/**
 * LaneView 车道组件（H17 / S1）
 *
 * 状态机：block_start → 找空 lane 占位（记 ts）→ block_done（chapter 匹配）→ 释放
 * 关键设计（plan 决策 3）：块与 lane 绑定后不被其它块覆盖 → 并发多块不闪烁
 * lane 数 = concurrency 动态渲染；超上限的块复用最早释放的 lane
 */

import { ref, computed, onMounted, onUnmounted } from 'vue'

interface Lane {
  blockId: number
  range: string
  startedAt: number
  status: 'running' | 'done' | 'failed'
}

const props = withDefaults(defineProps<{
  /** 起始 lane 数（来自 status.concurrency） */
  concurrency?: number
  /** 当前活跃块（start 时加，done/failed 时移除） */
  activeBlocks?: Map<number, { range: string; startedAt: number }>
  /** 已完成块（done=true:绿，done=false:红） */
  finishedBlocks?: Map<number, { ok: boolean; range: string }>
  /** tick 频率（毫秒），默认 1000 */
  tickMs?: number
}>(), {
  concurrency: 4,
  activeBlocks: () => new Map(),
  finishedBlocks: () => new Map(),
  tickMs: 1000,
})

const now = ref(Date.now())
let tickTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  tickTimer = setInterval(() => { now.value = Date.now() }, props.tickMs)
})
onUnmounted(() => {
  if (tickTimer !== null) clearInterval(tickTimer)
})

// 渲染 lanes：concurrency 个槽位；active 在前（按 start 时间），空槽占位在后
const lanes = computed<Lane[]>(() => {
  const active = Array.from(props.activeBlocks.entries())
    .sort((a, b) => a[1].startedAt - b[1].startedAt)
    .map(([blockId, info]) => ({
      blockId,
      range: info.range,
      startedAt: info.startedAt,
      status: 'running' as const,
    }))

  const result: Lane[] = active.slice(0, props.concurrency)
  // 用 finished 补齐空槽（最近 N 个 finished 块，占位视觉）
  const finished = Array.from(props.finishedBlocks.entries())
    .sort((a, b) => b[0] - a[0])  // blockId 大者更新
    .map(([blockId, info]) => ({
      blockId,
      range: info.range,
      startedAt: 0,  // 已完成不显示耗时
      status: info.ok ? 'done' as const : 'failed' as const,
    }))
  for (const f of finished) {
    if (result.length >= props.concurrency) break
    result.push(f)
  }
  // 不足补空槽
  while (result.length < props.concurrency) {
    result.push({ blockId: -1, range: '', startedAt: 0, status: 'running' })
  }
  return result
})

function elapsedStr(startedAt: number): string {
  if (startedAt === 0) return ''
  const sec = Math.floor((now.value - startedAt) / 1000)
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m${sec % 60}s`
}

function statusClass(status: Lane['status'], blockId: number): string {
  if (blockId === -1) return 'lane-empty'
  if (status === 'running') return 'lane-running'
  if (status === 'done') return 'lane-done'
  return 'lane-failed'
}
</script>

<template>
  <div class="lane-view">
    <div class="lane-head">
      <span class="lane-title">并发车道</span>
      <span class="lane-sub">{{ activeBlocks.size }} / {{ concurrency }} 运行中</span>
    </div>
    <div class="lane-list">
      <div
        v-for="(lane, i) in lanes"
        :key="i"
        :class="['lane-row', statusClass(lane.status, lane.blockId)]"
      >
        <div class="lane-slot">#{{ i + 1 }}</div>
        <div class="lane-range">
          <template v-if="lane.blockId === -1">— 空槽 —</template>
          <template v-else>{{ lane.range || `block ${lane.blockId}` }}</template>
        </div>
        <div class="lane-elapsed">{{ elapsedStr(lane.startedAt) }}</div>
        <div class="lane-state">
          <template v-if="lane.blockId === -1">—</template>
          <template v-else-if="lane.status === 'running'">⏳</template>
          <template v-else-if="lane.status === 'done'">✓</template>
          <template v-else>✗</template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lane-view {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.lane-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.lane-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--win-text-primary);
}
.lane-sub {
  font-size: 11px;
  color: var(--win-text-tertiary);
}
.lane-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.lane-row {
  display: grid;
  grid-template-columns: 36px 1fr 56px 24px;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 12px;
  background: var(--win-bg-secondary, rgba(255,255,255,0.04));
  border: 1px solid var(--win-border-secondary, rgba(255,255,255,0.08));
  transition: background 0.2s;
}
.lane-row.lane-running {
  border-color: var(--win-accent, #4f46e5);
  background: rgba(79, 70, 229, 0.08);
}
.lane-row.lane-done {
  border-color: rgba(34, 197, 94, 0.4);
  background: rgba(34, 197, 94, 0.06);
}
.lane-row.lane-failed {
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.06);
}
.lane-row.lane-empty {
  opacity: 0.4;
  border-style: dashed;
}
.lane-slot {
  font-size: 10px;
  color: var(--win-text-tertiary);
  font-family: ui-monospace, monospace;
}
.lane-range {
  color: var(--win-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lane-elapsed {
  font-size: 11px;
  color: var(--win-text-tertiary);
  font-family: ui-monospace, monospace;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.lane-state {
  font-size: 14px;
  text-align: center;
}
</style>
