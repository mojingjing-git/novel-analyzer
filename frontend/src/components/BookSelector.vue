<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api, type BookInfo } from '../api/client'
import Icon from './Icon.vue'

const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const books = ref<BookInfo[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try { const data = await api.listBooks(); books.value = data }
  catch (e) { console.error('加载书目失败:', e) }
  finally { loading.value = false }
}

function onChange(e: Event) {
  emit('update:modelValue', (e.target as HTMLSelectElement).value)
}

onMounted(load)
</script>

<template>
  <div class="book-selector">
    <select :value="modelValue" @change="onChange" class="glass-select flex-1">
      <option value="">选择书目...</option>
      <option v-for="b in books" :key="b.id" :value="b.id">
        {{ b.name }} ({{ b.total_chapters }} 章){{ b.has_report ? ' ✓已总结' : '' }}{{ b.has_aggregated ? ' ✓已聚合' : '' }}
      </option>
    </select>
    <button @click="load" :disabled="loading" class="glass-button btn-compact">
      <span v-if="loading" class="ios-spinner"></span>
      <Icon v-else name="refresh" :size="13" />
      <span>{{ loading ? '加载中' : '刷新' }}</span>
    </button>
  </div>
</template>

<style scoped>
.book-selector {
  display: flex;
  align-items: center;
  gap: 8px;
}
.btn-compact {
  padding: 7px 12px;
}
.ios-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 1.6px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>