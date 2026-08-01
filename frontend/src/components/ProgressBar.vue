<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  current: number
  total: number
  eta?: string
  label?: string
  blockSize?: number  // 用于换算章节进度
}>()

const percent = computed(() => {
  if (!props.total || props.total <= 0) return 0
  return Math.min(100, Math.round((props.current / props.total) * 100))
})

const chapterInfo = computed(() => {
  if (!props.blockSize || props.blockSize <= 1) return null
  return {
    current: props.current * props.blockSize,
    total: props.total * props.blockSize,
  }
})
</script>

<template>
  <div class="space-y-1.5">
    <div class="flex justify-between text-xs" style="color: var(--color-system-gray)">
      <span>
        {{ label || '进度' }}: {{ current }}/{{ total }}
        <span v-if="chapterInfo" style="color: var(--color-system-blue); margin-left: 6px">
          ({{ chapterInfo.current }}/{{ chapterInfo.total }} 章)
        </span>
      </span>
      <span v-if="eta">预计剩余: {{ eta }}</span>
    </div>
    <div class="glass-progress-track">
      <div class="glass-progress-fill" :style="{ width: `${percent}%` }" />
    </div>
  </div>
</template>