<script setup lang="ts">
/**
 * LaneView 车道组件（H17 / S1 + Phase 3 V2 升级）
 *
 * 状态机：block_start → 找空 lane 占位（记 ts）→ block_done（chapter 匹配）→ 释放
 * 关键设计（plan 决策 3）：块与 lane 绑定后不被其它块覆盖 → 并发多块不闪烁
 * lane 数 = concurrency 动态渲染；超上限的块复用最早释放的 lane
 *
 * H17 Phase 3 V2 升级（2026-08-26）：
 * - 进度条：基于 stallWarnSec 的归一化进度（0-100%）
 * - 卡死预警：超 stallWarnSec 无 token_delta 的 lane 变红 + ⚠
 */

import { ref, computed, onMounted, onUnmounted } from 'vue'

interface Lane {
  blockId: number
  range: string
  startedAt: number
  status: 'running' | 'done' | 'failed'
  lastTokenAt: number  // 最后一次 token_delta 时间戳（用于卡死检测）
  outputTokens: number  // 该车道累计 output tokens
}

const props = withDefaults(defineProps<{
  /** 起始 lane 数（来自 status.concurrency） */
  concurrency?: number
  /** 当前活跃块（start 时加，done/failed 时移除） */
  activeBlocks?: Map<number, { range: string; startedAt: number }>
  /** 已完成块（done=true:绿，done=false:红） */
  finishedBlocks?: Map<number, { ok: boolean; range: string }>
  /** 块级 token 累计（key: blockId, value: {outputTokens, lastTokenAt}） */
  tokenByBlock?: Map<number, { outputTokens: number; lastTokenAt: number }>
  /** 卡死预警阈值（秒），默认 480 */
  stallWarnSec?: number
  /** tick 频率（毫秒），默认 1000 */
  tickMs?: number
}>(), {
  concurrency: 4,
  activeBlocks: () => new Map(),
  finishedBlocks: () => new Map(),
  tokenByBlock: () => new Map(),
  stallWarnSec: 480,
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
    .map(([blockId, info]) => {
      const tk = props.tokenByBlock.get(blockId)
      return {
        blockId,
        range: info.range,
        startedAt: info.startedAt,
        status: 'running' as const,
        lastTokenAt: tk?.lastTokenAt ?? info.startedAt,
        outputTokens: tk?.outputTokens ?? 0,
      }
    })

  const result: Lane[] = active.slice(0, props.concurrency)
  // 用 finished 补齐空槽
  const finished = Array.from(props.finishedBlocks.entries())
    .sort((a, b) => b[0] - a[0])
    .map(([blockId, info]) => ({
      blockId,
      range: info.range,
      startedAt: 0,
      status: info.ok ? 'done' as const : 'failed' as const,
      lastTokenAt: 0,
      outputTokens: 0,
    }))
  for (const f of finished) {
    if (result.length >= props.concurrency) break
    result.push(f)
  }
  while (result.length < props.concurrency) {
    result.push({ blockId: -1, range: '', startedAt: 0, status: 'running', lastTokenAt: 0, outputTokens: 0 })
  }
  return result
})

function elapsedStr(startedAt: number): string {
  if (startedAt === 0) return ''
  const sec = Math.floor((now.value - startedAt) / 1000)
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m${sec % 60}s`
}

// 进度 0-1：基于 stallWarnSec 归一化（V2 升级：替代早期 0/100 离散显示）
function progressFrac(startedAt: number): number {
  if (startedAt === 0) return 0
  const sec = (now.value - startedAt) / 1000
  return Math.min(sec / props.stallWarnSec, 1)
}

// 卡死判定：超过 stallWarnSec 且无 token_delta 更新（H17 Phase 3）
function isStalled(lane: Lane): boolean {
  if (lane.status !== 'running' || lane.startedAt === 0) return false
  const elapsedSec = (now.value - lane.startedAt) / 1000
  if (elapsedSec < props.stallWarnSec) return false
  // 卡死 = 已用超阈值 + 至少 5 秒无 token_delta（防止最后一刻还在动）
  return (now.value - lane.lastTokenAt) > 5000
}

function statusClass(lane: Lane): string {
  if (lane.blockId === -1) return 'lane-empty'
  if (lane.status === 'done') return 'lane-done'
  if (lane.status === 'failed') return 'lane-failed'
  if (isStalled(lane)) return 'lane-stalled'
  return 'lane-running'
}

const stalledCount = computed(() => lanes.value.filter(isStalled).length)
</script>

<template>
  <div class="lane-view">
    <div class="lane-head">
      <span class="lane-title">并发车道</span>
      <span class="lane-sub">
        {{ activeBlocks.size }} / {{ concurrency }} 运行中
        <template v-if="stalledCount > 0">
          · <span class="lane-warn">⚠ {{ stalledCount }} 卡死</span>
        </template>
      </span>
    </div>
    <div class="lane-list">
      <div
        v-for="(lane, i) in lanes"
        :key="i"
        :class="['lane-row', statusClass(lane)]"
      >
        <div class="lane-slot">#{{ i + 1 }}</div>
        <div class="lane-range">
          <template v-if="lane.blockId === -1">— 空槽 —</template>
          <template v-else>{{ lane.range || `block ${lane.blockId}` }}</template>
        </div>
        <div class="lane-elapsed">
          {{ elapsedStr(lane.startedAt) }}
          <span v-if="lane.status === 'running' && lane.outputTokens > 0" class="lane-tok">
            {{ lane.outputTokens }}tok
          </span>
        </div>
        <div class="lane-state">
          <template v-if="lane.blockId === -1">—</template>
          <template v-else-if="lane.status === 'running' && isStalled(lane)">⚠</template>
          <template v-else-if="lane.status === 'running'">⏳</template>
          <template v-else-if="lane.status === 'done'">✓</template>
          <template v-else>✗</template>
        </div>
        <div
          v-if="lane.status === 'running' && lane.startedAt > 0"
          class="lane-progress"
        >
          <div
            class="lane-progress-bar"
            :style="{ width: (progressFrac(lane.startedAt) * 100) + '%' }"
            :class="{ 'lane-progress-warn': isStalled(lane) }"
          />
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
.lane-warn {
  color: #ef4444;
  font-weight: 500;
}
.lane-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.lane-row {
  display: grid;
  grid-template-columns: 36px 1fr 90px 24px;
  grid-template-rows: auto auto;
  align-items: center;
  gap: 4px 8px;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 12px;
  background: var(--win-bg-secondary, rgba(255,255,255,0.04));
  border: 1px solid var(--win-border-secondary, rgba(255,255,255,0.08));
  transition: background 0.2s, border-color 0.2s;
}
.lane-row.lane-running {
  border-color: var(--win-accent, #4f46e5);
  background: rgba(79, 70, 229, 0.08);
}
.lane-row.lane-stalled {
  border-color: #ef4444;
  background: rgba(239, 68, 68, 0.12);
  animation: lane-pulse 1.5s ease-in-out infinite;
}
@keyframes lane-pulse {
  0%, 100% { background: rgba(239, 68, 68, 0.12); }
  50% { background: rgba(239, 68, 68, 0.22); }
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
  grid-row: 1;
}
.lane-range {
  color: var(--win-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  grid-row: 1;
}
.lane-elapsed {
  font-size: 11px;
  color: var(--win-text-tertiary);
  font-family: ui-monospace, monospace;
  text-align: right;
  font-variant-numeric: tabular-nums;
  grid-row: 1;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 1px;
}
.lane-tok {
  font-size: 9px;
  color: var(--win-text-tertiary);
  font-weight: 500;
}
.lane-state {
  font-size: 14px;
  text-align: center;
  grid-row: 1;
}
.lane-progress {
  grid-column: 1 / -1;
  grid-row: 2;
  height: 3px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 2px;
  overflow: hidden;
  margin-top: 2px;
}
.lane-progress-bar {
  height: 100%;
  background: var(--win-accent, #4f46e5);
  transition: width 0.3s linear, background 0.3s;
}
.lane-progress-bar.lane-progress-warn {
  background: #ef4444;
}
</style>
