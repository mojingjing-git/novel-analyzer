<script setup lang="ts">
/**
 * RunDashboard 运行中实时仪表盘（H17 / S1）
 *
 * 容器组件：聚合指标 + LaneView + DiscoveryFeed
 * 数据源：父组件传入的 activeBlocks / finishedBlocks / discoveries / tokens / progress
 * 自身订阅 WS（通过 props 传入的 onMessage 回调注册）
 */

import { computed } from 'vue'
import CountUp from './CountUp.vue'
import LaneView from './LaneView.vue'
import DiscoveryFeed from './DiscoveryFeed.vue'

export interface ActiveBlock {
  range: string
  startedAt: number
}
export interface FinishedBlock {
  ok: boolean
  range: string
}
export interface DiscoveryItem {
  id: number
  ts: number
  events: number
  foreshadows: string[]
  characters: string[]
  unresolved: string[]
  highlighted: boolean
}

const props = withDefaults(defineProps<{
  /** 是否正在运行（控制显示） */
  running?: boolean
  /** 并发槽位数（来自 status.concurrency） */
  concurrency?: number
  /** 进度 */
  progress?: { current: number; total: number; eta: string }
  /** 本会话 token 统计（input / output） */
  sessionTokens?: { input: number; output: number } | null
  /** 活跃块 */
  activeBlocks?: Map<number, ActiveBlock>
  /** 已完成块（保留最近 N 个用于车道占位） */
  finishedBlocks?: Map<number, FinishedBlock>
  /** 发现流 */
  discoveries?: DiscoveryItem[]
}>(), {
  running: false,
  concurrency: 4,
  progress: () => ({ current: 0, total: 0, eta: '' }),
  sessionTokens: null,
  activeBlocks: () => new Map(),
  finishedBlocks: () => new Map(),
  discoveries: () => [],
})

const inputTokens = computed(() => props.sessionTokens?.input ?? 0)
const outputTokens = computed(() => props.sessionTokens?.output ?? 0)
const completed = computed(() => props.progress?.current ?? 0)
const total = computed(() => props.progress?.total ?? 0)
const eta = computed(() => props.progress?.eta ?? '—')

function fmtNum(n: number): string {
  if (n >= 10000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}
</script>

<template>
  <div class="run-dashboard">
    <!-- ① 聚合指标行（4 卡） -->
    <div class="rd-aggregate">
      <div class="rd-card">
        <div class="rd-label">已完成</div>
        <div class="rd-value">
          <CountUp :value="completed" :format="fmtNum" />
          <span class="rd-suffix">/ {{ fmtNum(total) }}</span>
        </div>
      </div>
      <div class="rd-card">
        <div class="rd-label">ETA</div>
        <div class="rd-value rd-eta">{{ eta }}</div>
      </div>
      <div class="rd-card">
        <div class="rd-label">本会话输入</div>
        <div class="rd-value">
          <CountUp :value="inputTokens" :format="fmtNum" />
          <span class="rd-suffix">tok</span>
        </div>
      </div>
      <div class="rd-card">
        <div class="rd-label">本会话输出</div>
        <div class="rd-value">
          <CountUp :value="outputTokens" :format="fmtNum" />
          <span class="rd-suffix">tok</span>
        </div>
      </div>
    </div>

    <!-- ② 并发车道 -->
    <LaneView
      :concurrency="concurrency"
      :active-blocks="activeBlocks"
      :finished-blocks="finishedBlocks"
    />

    <!-- ③ 发现流 -->
    <DiscoveryFeed :items="discoveries" />
  </div>
</template>

<style scoped>
.run-dashboard {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 12px 4px;
}
.rd-aggregate {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
.rd-card {
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--win-bg-secondary, rgba(255,255,255,0.04));
  border: 1px solid var(--win-border-secondary, rgba(255,255,255,0.08));
}
.rd-label {
  font-size: 10px;
  color: var(--win-text-tertiary);
  margin-bottom: 2px;
}
.rd-value {
  display: flex;
  align-items: baseline;
  gap: 4px;
  font-size: 18px;
  font-weight: 600;
  color: var(--win-text-primary);
  font-variant-numeric: tabular-nums;
}
.rd-eta {
  font-size: 14px;
  font-weight: 500;
  font-family: ui-monospace, monospace;
}
.rd-suffix {
  font-size: 11px;
  color: var(--win-text-tertiary);
  font-weight: 400;
}
</style>
