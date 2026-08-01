<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import BookSelector from '../components/BookSelector.vue'
import { api } from '../api/client'

interface TimelineEvent {
  chapter: number
  id?: string
  event: string
  characters: string | string[]
  function: string
}

interface Foreshadow {
  chapter: number
  clue: string
  type: string
  confidence: number
}

const bookId = ref('')
const mode = ref<'events' | 'foreshadows'>('events')
const events = ref<TimelineEvent[]>([])
const foreshadows = ref<Foreshadow[]>([])

watch(bookId, async () => {
  if (!bookId.value) return
  try {
    const res = await api.getTimeline(bookId.value)
    events.value = res.events as TimelineEvent[]
    foreshadows.value = res.foreshadows as Foreshadow[]
  } catch (e) { console.error(e) }
})

const chapters = computed(() => {
  const map = new Map<number, { events: TimelineEvent[]; foreshadows: Foreshadow[] }>()
  if (mode.value === 'events') {
    for (const ev of events.value) {
      if (!map.has(ev.chapter)) map.set(ev.chapter, { events: [], foreshadows: [] })
      map.get(ev.chapter)!.events.push(ev)
    }
  } else {
    for (const fs of foreshadows.value) {
      if (!map.has(fs.chapter)) map.set(fs.chapter, { events: [], foreshadows: [] })
      map.get(fs.chapter)!.foreshadows.push(fs)
    }
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0])
})

const typeColors: Record<string, string> = {
  plant: 'bg-green-100 text-green-700 border-green-300',
  recall: 'bg-blue-100 text-blue-700 border-blue-300',
  develop: 'bg-yellow-100 text-yellow-700 border-yellow-300',
  resolve: 'bg-purple-100 text-purple-700 border-purple-300',
}

const typeLabels: Record<string, string> = {
  plant: '埋设',
  recall: '回收',
  develop: '发展',
  resolve: '揭晓',
}

function charStr(c: string | string[]): string {
  if (Array.isArray(c)) return c.join(', ')
  return c
}
</script>

<template>
  <div class="space-y-4 p-4">
    <h2 class="section-title">时间线</h2>
    <BookSelector v-model="bookId" />
    <div v-if="bookId" class="flex gap-2">
      <button @click="mode = 'events'" :class="mode === 'events' ? 'bg-cyan-400 text-white' : 'border border-gray-300 text-gray-600'" class="px-3 py-1 rounded text-sm">事件时间线 ({{ events.length }})</button>
      <button @click="mode = 'foreshadows'" :class="mode === 'foreshadows' ? 'bg-cyan-400 text-white' : 'border border-gray-300 text-gray-600'" class="px-3 py-1 rounded text-sm">伏笔时间线 ({{ foreshadows.length }})</button>
    </div>
    <div v-if="!bookId" class="text-gray-400">请选择书目</div>
    <div v-else-if="chapters.length === 0" class="text-gray-400">暂无数据</div>
    <div v-else class="space-y-3">
      <div v-for="[ch, group] in chapters" :key="ch" class="flex gap-3">
        <div class="shrink-0 w-16 text-right">
          <div class="inline-block bg-cyan-50 text-cyan-600 text-xs font-medium px-2 py-1 rounded">第{{ ch }}章</div>
        </div>
        <div class="flex-1 space-y-2">
          <template v-if="mode === 'events'">
            <div v-for="(ev, idx) in group.events" :key="idx" class="bg-white border border-gray-200 rounded-lg p-3 text-sm">
              <div class="flex items-start gap-2">
                <span class="text-xs text-gray-400 mt-0.5">#{{ idx + 1 }}</span>
                <div class="flex-1">
                  <p class="text-gray-800">{{ ev.event }}</p>
                  <div class="flex gap-3 mt-1 text-xs text-gray-500">
                    <span v-if="charStr(ev.characters)">角色: {{ charStr(ev.characters) }}</span>
                    <span v-if="ev.function">功能: {{ ev.function }}</span>
                  </div>
                </div>
              </div>
            </div>
          </template>
          <template v-else>
            <div v-for="(fs, idx) in group.foreshadows" :key="idx" :class="['rounded-lg border p-3 text-sm', typeColors[fs.type] || 'bg-gray-50 border-gray-200 text-gray-700']">
              <div class="flex items-start gap-2">
                <span class="text-xs font-medium mt-0.5">{{ typeLabels[fs.type] || fs.type }}</span>
                <div class="flex-1">
                  <p>{{ fs.clue }}</p>
                  <div class="mt-1 text-xs opacity-70">置信度: {{ (fs.confidence * 100).toFixed(0) }}%</div>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
