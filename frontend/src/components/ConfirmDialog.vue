<script setup lang="ts">
const props = defineProps<{
  title?: string
  message: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}>()

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()

function onConfirm() { emit('confirm') }
function onCancel() { emit('cancel') }
</script>

<template>
  <Transition name="modal">
    <div class="confirm-mask" @click.self="onCancel">
      <div class="confirm-card glass-elevated">
        <div class="confirm-body">
          <h3 v-if="title" class="confirm-title">{{ title }}</h3>
          <p class="confirm-message">{{ message }}</p>
        </div>
        <div class="confirm-actions">
          <button @click="onCancel" class="glass-button">{{ cancelText || '取消' }}</button>
          <button
            @click="onConfirm"
            :class="danger ? 'glass-button glass-button-danger' : 'glass-button glass-button-primary'"
          >{{ confirmText || '确认' }}</button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.confirm-mask {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.3);
}

.confirm-card {
  max-width: 360px;
  width: calc(100% - 32px);
  border-radius: 20px;
  overflow: hidden;
}

.confirm-body {
  padding: 22px 24px 16px;
}

.confirm-title {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin: 0;
  color: var(--win-text-primary);
}

.confirm-message {
  font-size: 13px;
  color: var(--win-text-secondary);
  line-height: 1.5;
  margin: 8px 0 0;
}

.confirm-actions {
  display: flex;
  gap: 8px;
  padding: 0 16px 16px;
  justify-content: flex-end;
}
</style>