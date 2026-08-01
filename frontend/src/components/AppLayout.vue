<script setup lang="ts">
import { ref, onMounted, nextTick, watch } from 'vue'
import { useRoute, RouterView } from 'vue-router'
import Icon from './Icon.vue'

const route = useRoute()
const isDark = ref(false)
const navListRef = ref<HTMLElement | null>(null)

const navItems = [
  { path: '/', label: '分析队列', icon: 'queue' },
  { path: '/characters', label: '角色卡', icon: 'characters' },
  { path: '/summary', label: '最终总结', icon: 'summary' },
  { path: '/splitter', label: '小说切分', icon: 'splitter' },
  { path: '/workspace', label: '工作区', icon: 'workspace' },
  { path: '/prompt', label: 'Prompt预览', icon: 'prompt' },
  { path: '/style', label: '风格分析', icon: 'style' },
  { path: '/stats', label: '统计面板', icon: 'stats' },
  { path: '/timeline', label: '时间线', icon: 'timeline' },
  { path: '/graph', label: '关系图', icon: 'graph' },
  { path: '/map', label: '地图', icon: 'map' },
  { path: '/settings', label: '设置', icon: 'settings' },
]

function toggleTheme() {
  isDark.value = !isDark.value
  if (isDark.value) document.documentElement.classList.add('dark')
  else document.documentElement.classList.remove('dark')
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
}

// 浅玻璃滑动指示器（不是实色蓝块）
// 用纯 DOM 操作：find active link → calculate position → 直接 style.transform
let rafScheduled = false
let updateRetryCount = 0

function updateIndicator() {
  rafScheduled = false
  const container = navListRef.value
  if (!container) {
    if (updateRetryCount < 60) {
      updateRetryCount++
      requestAnimationFrame(updateIndicator)
    }
    return
  }

  const activeLink = container.querySelector(`a[href="${CSS.escape(route.path)}"]`) as HTMLElement
  const indicator = container.querySelector('.nav-indicator') as HTMLElement

  if (!activeLink || !indicator) {
    if (updateRetryCount < 60) {
      updateRetryCount++
      requestAnimationFrame(updateIndicator)
    }
    return
  }
  updateRetryCount = 0

  const linkRect = activeLink.getBoundingClientRect()
  const containerRect = container.getBoundingClientRect()
  if (linkRect.height === 0) {
    requestAnimationFrame(updateIndicator)
    return
  }

  const targetY = linkRect.top - containerRect.top
  const height = linkRect.height
  // 直接 DOM 操作
  indicator.style.transform = `translateY(${targetY}px)`
  indicator.style.height = `${height}px`
  indicator.style.opacity = '1'
}

function scheduleUpdate() {
  if (rafScheduled) return
  rafScheduled = true
  requestAnimationFrame(() => requestAnimationFrame(updateIndicator))
}

onMounted(() => {
  const saved = localStorage.getItem('theme')
  if (saved === 'dark') {
    isDark.value = true
    document.documentElement.classList.add('dark')
  }
  scheduleUpdate()

  let resizeTimer: number | null = null
  window.addEventListener('resize', () => {
    if (resizeTimer) clearTimeout(resizeTimer)
    resizeTimer = window.setTimeout(scheduleUpdate, 100)
  })

  const container = navListRef.value
  if (container) {
    container.addEventListener('scroll', scheduleUpdate, { passive: true })
  }
})

watch(() => route.path, scheduleUpdate, { flush: 'post' })
</script>

<template>
  <div class="layout-root">
    <!-- 侧边栏：液态玻璃面板（不是占满屏幕，留出右侧间隙） -->
    <aside class="sidebar glass-panel">
      <!-- 顶部独立玻璃子面板（与下方菜单分隔） -->
      <div class="sidebar-brand glass-subtle">
        <div class="logo-mark">
          <Icon name="queue" :size="16" />
        </div>
        <div>
          <h1 class="brand-name">小说分析器</h1>
          <p class="brand-sub">Novel Analyzer</p>
        </div>
      </div>

      <!-- 导航菜单 -->
      <nav class="nav-list" ref="navListRef">
        <!-- 浅玻璃滑动指示器（放在 nav-list 内部，跟随滚动且参照系一致） -->
        <div class="nav-indicator"></div>

        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          active-class="is-active"
          :exact-active-class="item.path === '/' ? 'is-active' : ''"
        >
          <span class="nav-icon-wrap">
            <Icon :name="item.icon" :size="17" />
          </span>
          <span class="nav-label">{{ item.label }}</span>
        </router-link>
      </nav>

      <!-- 底部主题切换 -->
      <button class="theme-toggle" @click="toggleTheme">
        <Icon :name="isDark ? 'sun' : 'moon'" :size="13" />
        <span>{{ isDark ? '亮色' : '暗色' }}</span>
      </button>
    </aside>

    <!-- 主内容区 -->
    <main class="main-area">
      <RouterView v-slot="{ Component, route }">
        <transition name="page" mode="out-in">
          <component :is="Component" :key="route.path" />
        </transition>
      </RouterView>
    </main>
  </div>
</template>

<style scoped>
.layout-root {
  display: flex;
  height: 100%;
  gap: 12px;
  padding: 12px;
}

/* ===== 侧边栏容器（玻璃面板） ===== */
.sidebar {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 10px;
  gap: 6px;
  /* 圆角统一 22px（按规范） */
  border-radius: 22px;
}

/* ===== 顶部品牌子面板（玻璃子容器） ===== */
.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 12px;
  border-radius: 14px;
}

.logo-mark {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: linear-gradient(135deg, #007AFF, #5AC8FA);
  box-shadow:
    0 4px 12px rgba(0, 122, 255, 0.28),
    inset 0 1px 0 rgba(255, 255, 255, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  flex-shrink: 0;
}

.brand-name {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin: 0;
  color: var(--text-primary);
  line-height: 1.2;
}

.brand-sub {
  font-size: 10px;
  color: var(--text-secondary);
  margin: 2px 0 0;
  letter-spacing: 0.02em;
}

/* ===== 导航列表 ===== */
.nav-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 4px;
  position: relative;
  /* 自定义极细滚动条 */
  scrollbar-width: thin;
}

/* ===== 浅玻璃滑动指示器（核心：低透明度蓝色基底 + 内发光） ===== */
.nav-indicator {
  position: absolute;
  left: 4px;
  right: 4px;
  border-radius: 10px;
  /* 低透明度蓝色基底（规范要求 0.10-0.18） */
  background: rgba(35, 130, 255, 0.13);
  /* 细微内发光 + 边缘光带 */
  box-shadow:
    inset 0 0 0 0.5px rgba(255, 255, 255, 0.18),
    inset 0 1px 0 rgba(255, 255, 255, 0.25),
    inset 0 -1px 0 rgba(35, 130, 255, 0.05);
  /* 边缘细微高光带 */
  border-top: 1px solid rgba(255, 255, 255, 0.18);
  /* 流体阻尼 */
  transition:
    transform 380ms cubic-bezier(0.2, 0, 0.2, 1),
    height 240ms cubic-bezier(0.2, 0, 0.2, 1),
    opacity 320ms cubic-bezier(0.2, 0, 0.2, 1);
  opacity: 0;
  pointer-events: none;
  z-index: 0;
  height: 34px;
  transform: translateY(6px);
  top: 0;
}

html.dark .nav-indicator {
  background: rgba(35, 130, 255, 0.18);
  box-shadow:
    inset 0 0 0 0.5px rgba(255, 255, 255, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.18);
}

/* ===== 单个导航项 ===== */
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  margin: 2px 0;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  text-decoration: none;
  -webkit-tap-highlight-color: transparent;
  cursor: pointer;
  outline: none !important;
  position: relative;
  z-index: 1;
  transition:
    color 240ms cubic-bezier(0.2, 0, 0.2, 1),
    background 240ms cubic-bezier(0.2, 0, 0.2, 1);
}

/* Hover：浅玻璃底色浮现 */
.nav-item:hover {
  background: rgba(255, 255, 255, 0.18);
  color: var(--text-primary);
}
html.dark .nav-item:hover {
  background: rgba(255, 255, 255, 0.06);
}

.nav-icon-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  color: var(--text-secondary);
  transition:
    color 240ms cubic-bezier(0.2, 0, 0.2, 1),
    background 240ms cubic-bezier(0.2, 0, 0.2, 1);
}

.nav-item:hover .nav-icon-wrap {
  color: var(--text-primary);
}

/* 激活态：文字变深、图标变蓝（不要大面积蓝底！蓝色由指示器提供） */
.nav-item.is-active {
  color: var(--text-primary);
  font-weight: 600;
  background: transparent !important;  /* 让指示器透出来 */
}

.nav-item.is-active .nav-icon-wrap {
  color: var(--color-system-blue);
  background: rgba(35, 130, 255, 0.16);
}

/* ===== 底部主题切换 ===== */
.theme-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  background: transparent;
  border: 1px solid var(--divider);
  border-radius: 12px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 220ms cubic-bezier(0.2, 0, 0.2, 1);
  font-family: inherit;
  margin-top: auto;
}
.theme-toggle:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-primary);
  border-color: transparent;
}

/* ===== 主内容区（无边框、通栏） ===== */
.main-area {
  flex: 1;
  overflow-y: auto;
  border-radius: 22px;
  position: relative;
}
</style>