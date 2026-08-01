<script setup lang="ts">
import { ref, watch } from 'vue'
import BookSelector from '../components/BookSelector.vue'
import { api } from '../api/client'

interface CharSummary { name: string; first_appearance?: number; total_events: number; chapters_count: number }
interface CharArc { chapter: number; surface_action: string; inner_motivation: string; change_delta: string; driver: string }
interface CharEvent { chapter: number; event: string; function: string }
interface CharRel { first_encounter: number; joint_events: { chapter: number; event: string; function: string }[]; event_count: number }

const bookId = ref('')
const characters = ref<CharSummary[]>([])
const loadingChars = ref(false)
const card = ref<{
  name: string
  first_appearance?: number | null
  chapters: number[]
  total_events: number
  arcs: CharArc[]
  events: CharEvent[]
  states: Record<string, string>
  relationships: Record<string, CharRel>
} | null>(null)
const loadingCard = ref(false)
const selectedName = ref('')
const cardError = ref('')

async function loadCharacters() {
  characters.value = []
  card.value = null
  selectedName.value = ''
  if (!bookId.value) return
  loadingChars.value = true
  try {
    const res = await api.getBookCharacters(bookId.value)
    characters.value = (res.characters as CharSummary[]) || []
  } catch (e) {
    console.error(e)
    alert('加载角色列表失败: ' + (e as Error).message)
  } finally {
    loadingChars.value = false
  }
}

async function viewCard(name: string) {
  if (!bookId.value) return
  selectedName.value = name
  card.value = null
  cardError.value = ''
  loadingCard.value = true
  try {
    const res = await api.getCharacterCard(bookId.value, name)
    card.value = res.character as any
  } catch (e) {
    cardError.value = '加载角色卡失败: ' + (e as Error).message
  } finally {
    loadingCard.value = false
  }
}

// 把 [1,2,3,5,6,8] 压缩成 "1-3、5-6、8"
function formatChapters(chapters: number[]): string {
  if (!chapters || !chapters.length) return '无'
  const sorted = [...chapters].sort((a, b) => a - b)
  const ranges: string[] = []
  let start = sorted[0]
  let prev = sorted[0]
  for (let i = 1; i <= sorted.length; i++) {
    const cur = sorted[i]
    if (cur === prev + 1) {
      prev = cur
      continue
    }
    ranges.push(start === prev ? `第${start}章` : `第${start}-${prev}章`)
    start = cur
    prev = cur as number
  }
  return ranges.join('、')
}

function sortedStates(states: Record<string, string>): { chapter: number; state: string }[] {
  if (!states) return []
  return Object.entries(states)
    .map(([k, v]) => ({ chapter: Number(k), state: v }))
    .sort((a, b) => a.chapter - b.chapter)
}

function topRelationships(rels: Record<string, CharRel>, n = 6): { name: string; rel: CharRel }[] {
  if (!rels) return []
  return Object.entries(rels)
    .map(([name, rel]) => ({ name, rel }))
    .sort((a, b) => b.rel.event_count - a.rel.event_count)
    .slice(0, n)
}

watch(bookId, loadCharacters)
</script>

<template>
  <div class="space-y-4">
    <h2 class="section-title">角色卡</h2>
    <p class="section-subtitle">从聚合数据中查看每个角色的出场、弧光、事件与关系网。</p>

    <BookSelector v-model="bookId" />

    <!-- 角色列表 -->
    <div v-if="loadingChars" class="text-sm" style="color: var(--text-tertiary)">加载角色列表中…</div>
    <div v-else-if="characters.length" class="flex gap-2 flex-wrap">
      <button
        v-for="c in characters"
        :key="c.name"
        @click="viewCard(c.name)"
        class="glass-button"
        :class="{ 'glass-button-primary': selectedName === c.name }"
      >
        {{ c.name }}
        <span class="ml-1 opacity-70 text-xs">{{ c.total_events }} 事件</span>
      </button>
    </div>
    <div v-else-if="bookId" class="text-sm" style="color: var(--text-tertiary)">
      该书目暂无角色数据（请先运行「数据聚合」生成 character_tracking 聚合文件）。
    </div>

    <!-- 角色卡详情 -->
    <div v-if="loadingCard" class="glass-card"><div class="text-sm" style="color: var(--text-tertiary)">生成角色卡中…</div></div>
    <div v-else-if="cardError" class="glass-card" style="color: var(--color-system-red, #e5484d)">{{ cardError }}</div>

    <div v-else-if="card" class="glass-card space-y-5">
      <div class="flex items-baseline justify-between">
        <h3 class="text-lg font-semibold" style="color: var(--text-primary)">{{ card.name }}</h3>
        <div class="text-xs" style="color: var(--text-tertiary)">
          首次出现：第{{ card.first_appearance ?? '未知' }}章 · 共 {{ card.total_events }} 事件
        </div>
      </div>

      <!-- 基本信息 -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="glass-stat">
          <div class="glass-stat-label">首次出现</div>
          <div class="glass-stat-value">第{{ card.first_appearance ?? '—' }}章</div>
        </div>
        <div class="glass-stat">
          <div class="glass-stat-label">出场章节</div>
          <div class="glass-stat-value">{{ card.chapters.length }} 章</div>
        </div>
        <div class="glass-stat">
          <div class="glass-stat-label">参与事件</div>
          <div class="glass-stat-value">{{ card.total_events }}</div>
        </div>
        <div class="glass-stat">
          <div class="glass-stat-label">关系数</div>
          <div class="glass-stat-value">{{ Object.keys(card.relationships || {}).length }}</div>
        </div>
      </div>

      <!-- 出场章节 -->
      <div v-if="card.chapters.length">
        <div class="block-title">出场章节</div>
        <div class="text-sm" style="color: var(--text-primary)">{{ formatChapters(card.chapters) }}</div>
      </div>

      <!-- 人物弧光 -->
      <div v-if="card.arcs.length">
        <div class="block-title">人物弧光（{{ card.arcs.length }}）</div>
        <div class="space-y-2">
          <div v-for="arc in card.arcs" :key="arc.chapter" class="arc-item">
            <div class="font-medium" style="color: var(--text-primary)">第{{ arc.chapter }}章</div>
            <div class="text-xs mt-1" style="color: var(--text-secondary)">
              <span class="arc-key">表面行为</span>{{ arc.surface_action }}
            </div>
            <div class="text-xs mt-0.5" style="color: var(--text-secondary)">
              <span class="arc-key">内在动机</span>{{ arc.inner_motivation }}
            </div>
            <div class="text-xs mt-0.5" style="color: var(--text-secondary)">
              <span class="arc-key">变化幅度</span>{{ arc.change_delta }}
              <span class="arc-key ml-2">驱动</span>{{ arc.driver }}
            </div>
          </div>
        </div>
      </div>

      <!-- 主要事件 -->
      <div v-if="card.events.length">
        <div class="block-title">主要事件（{{ card.events.length }}）</div>
        <div class="space-y-1">
          <div v-for="(ev, i) in card.events" :key="i" class="event-item">
            <span class="text-xs" style="color: var(--color-system-blue)">第{{ ev.chapter }}章</span>
            <span class="text-sm ml-2" style="color: var(--text-primary)">{{ ev.event }}</span>
            <span class="text-xs ml-2 opacity-70" style="color: var(--text-tertiary)">功能：{{ ev.function }}</span>
          </div>
        </div>
      </div>

      <!-- 状态演变 -->
      <div v-if="sortedStates(card.states).length">
        <div class="block-title">状态演变</div>
        <div class="space-y-0.5">
          <div v-for="s in sortedStates(card.states)" :key="s.chapter" class="text-xs" style="color: var(--text-secondary)">
            <span class="opacity-70">第{{ s.chapter }}章：</span>{{ s.state }}
          </div>
        </div>
      </div>

      <!-- 人际关系 -->
      <div v-if="topRelationships(card.relationships).length">
        <div class="block-title">人际关系</div>
        <div class="space-y-2">
          <div v-for="r in topRelationships(card.relationships)" :key="r.name" class="rel-item">
            <div class="flex items-baseline justify-between">
              <span class="font-medium" style="color: var(--text-primary)">{{ r.name }}</span>
              <span class="text-xs" style="color: var(--text-tertiary)">共同事件 {{ r.rel.event_count }} 个</span>
            </div>
            <div class="text-xs mt-1 opacity-80" style="color: var(--text-secondary)">
              首次相遇：第{{ r.rel.first_encounter }}章
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.block-title {
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--color-system-gray);
  padding-bottom: 6px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--glass-border-subtle);
}
.glass-stat {
  background: var(--glass-fill-subtle);
  border: 1px solid var(--glass-border-subtle);
  border-radius: 12px;
  padding: 12px 14px;
}
.glass-stat-label {
  font-size: 0.72rem;
  color: var(--text-tertiary);
}
.glass-stat-value {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--text-primary);
  margin-top: 2px;
}
.arc-item, .rel-item {
  background: var(--glass-fill-subtle);
  border: 1px solid var(--glass-border-subtle);
  border-left: 3px solid var(--color-system-blue);
  border-radius: 10px;
  padding: 10px 12px;
}
.event-item {
  border-left: 3px solid var(--color-system-amber, #d9a23a);
  padding-left: 8px;
}
.arc-key {
  display: inline-block;
  min-width: 56px;
  color: var(--text-tertiary);
}
</style>
