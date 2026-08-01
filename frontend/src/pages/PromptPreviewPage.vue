<script setup lang="ts">
import { ref } from 'vue'
import BookSelector from '../components/BookSelector.vue'
import { api } from '../api/client'

const bookId = ref('')
const maxChars = ref(0)
const system = ref('')
const user = ref('')
const sysLen = ref(0)
const usrLen = ref(0)
const truncated = ref(false)
const totalChars = ref(0)

async function preview() {
  try {
    const res = await api.promptPreview(bookId.value, maxChars.value)
    system.value = res.system_prompt
    user.value = res.user_prompt
    sysLen.value = res.system_len
    usrLen.value = res.user_len
    truncated.value = res.chapter_truncated
    totalChars.value = res.chapter_total_chars
  } catch (e) { alert((e as Error).message) }
}
</script>

<template>
  <div class="space-y-4 p-4">
    <h2 class="section-title">Prompt 预览</h2>
    <div class="flex gap-2 items-center flex-wrap">
      <div class="flex-1 min-w-[200px]"><BookSelector v-model="bookId" /></div>
      <label class="text-sm flex items-center gap-1" style="color: var(--color-system-gray)">
        章节原文上限
        <input v-model.number="maxChars" type="number" min="0" step="500" class="glass-input" style="width: 110px" placeholder="0=不截断" />
      </label>
      <button @click="preview" :disabled="!bookId" class="px-3 py-1.5 bg-cyan-400 text-white rounded text-sm hover:bg-cyan-500 disabled:opacity-50">生成预览</button>
    </div>
    <p v-if="totalChars > 0" class="text-xs" style="color: var(--text-tertiary)">
      章节原文共 {{ totalChars }} 字符{{ truncated ? '（已截断预览）' : '' }}
    </p>
    <div v-if="system" class="space-y-2"><div class="text-sm text-gray-500">SYSTEM ({{ sysLen }} 字符)</div><pre class="bg-gray-900 text-green-400 p-3 rounded text-xs overflow-auto max-h-60">{{ system }}</pre></div>
    <div v-if="user" class="space-y-2"><div class="text-sm text-gray-500">USER ({{ usrLen }} 字符)</div><pre class="bg-gray-900 text-cyan-400 p-3 rounded text-xs overflow-auto max-h-60">{{ user }}</pre></div>
  </div>
</template>