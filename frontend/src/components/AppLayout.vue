<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, RouterView } from 'vue-router'
import Icon from './Icon.vue'

const route = useRoute()
const isDark = ref(false)
// 导航折叠：展开 280px / 收起 48px（只显示图标）
const collapsed = ref(false)
// 桌面端（pywebview）才渲染窗口控制按钮
const isDesktop = ref(false)

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

// 窗口控制（桌面 pywebview）。浏览器内不存在 pywebview，按钮不渲染。
const wvApi = () => (window as unknown as { pywebview?: { api?: Record<string, unknown> } })?.pywebview?.api
function winMin() { void (wvApi()?.minimize as (() => void) | undefined)?.() }
function winMax() { void (wvApi()?.toggle_maximize as (() => void) | undefined)?.() }
function winClose() { void (wvApi()?.close as (() => void) | undefined)?.() }

// 标题栏拖拽移动窗口（桌面端）：按下记录起点，移动按增量调用 pywebview move
let dragState: { sx: number; sy: number; lastX: number; lastY: number } | null = null
function onTitlebarDown(e: MouseEvent) {
  const api = wvApi()
  if (!api?.move) return
  if ((e.target as HTMLElement).closest('.tb-btn')) return // 不拖拽按钮
  dragState = { sx: e.screenX, sy: e.screenY, lastX: e.screenX, lastY: e.screenY }
  window.addEventListener('mousemove', onTitlebarMove)
  window.addEventListener('mouseup', onTitlebarUp)
}
function onTitlebarMove(e: MouseEvent) {
  if (!dragState) return
  const dx = e.screenX - dragState.lastX
  const dy = e.screenY - dragState.lastY
  dragState.lastX = e.screenX
  dragState.lastY = e.screenY
  if (dx !== 0 || dy !== 0) void (wvApi()?.move as ((x: number, y: number) => void) | undefined)?.(dx, dy)
}
function onTitlebarUp() {
  dragState = null
  window.removeEventListener('mousemove', onTitlebarMove)
  window.removeEventListener('mouseup', onTitlebarUp)
}

onMounted(() => {
  isDesktop.value = !!wvApi()
  if (new URLSearchParams(window.location.search).get('collapsed') === '1') {
    collapsed.value = true
  }
  const saved = localStorage.getItem('theme')
  if (saved === 'dark') {
    isDark.value = true
    document.documentElement.classList.add('dark')
  }
})
</script>

<template>
  <div class="layout-root">
    <!-- 一体化标题栏（48px，Mica，可拖拽移动窗口） -->
    <header class="titlebar mica-surface" @mousedown="onTitlebarDown">
      <div class="titlebar-brand">
        <div class="logo-mark logo-mark-sm">
          <Icon name="queue" :size="16" />
        </div>
        <span class="titlebar-title">小说分析器</span>
      </div>
      <div v-if="isDesktop" class="titlebar-controls">
        <button class="tb-btn" title="最小化" @click="winMin"><Icon name="minus" :size="10" /></button>
        <button class="tb-btn" title="最大化 / 还原" @click="winMax"><Icon name="square" :size="9" /></button>
        <button class="tb-btn tb-close" title="关闭" @click="winClose"><Icon name="x" :size="10" /></button>
      </div>
    </header>

    <div class="layout-body">
      <!-- Win11 NavigationView 侧边栏：Mica 基底（背景层），内容卡片白色浮于其上 -->
      <aside class="sidebar mica-surface" :class="{ collapsed }">
        <nav class="nav-list">
          <router-link
            v-for="item in navItems"
            :key="item.path"
            :to="item.path"
            class="nav-item"
            active-class="is-active"
            :exact-active-class="item.path === '/' ? 'is-active' : ''"
            :title="collapsed ? item.label : undefined"
          >
            <span class="nav-icon-wrap">
              <Icon :name="item.icon" :size="16" />
            </span>
            <span v-if="!collapsed" class="nav-label">{{ item.label }}</span>
          </router-link>
        </nav>

        <div class="sidebar-footer">
          <button class="nav-item foot-item" @click="collapsed = !collapsed" :title="collapsed ? '展开导航' : '收起导航'">
            <span class="nav-icon-wrap">
              <Icon :name="collapsed ? 'plus' : 'x'" :size="16" />
            </span>
            <span v-if="!collapsed" class="nav-label">折叠导航</span>
          </button>
          <button class="nav-item foot-item" @click="toggleTheme">
            <span class="nav-icon-wrap">
              <Icon :name="isDark ? 'sun' : 'moon'" :size="16" />
            </span>
            <span v-if="!collapsed" class="nav-label">{{ isDark ? '亮色' : '暗色' }}</span>
          </button>
        </div>
      </aside>

      <!-- 主内容区：Mica 基底，24px 内边距，独立滚动 -->
      <main class="main-area">
        <RouterView v-slot="{ Component, route }">
          <transition name="page" mode="out-in">
            <component :is="Component" :key="route.path" />
          </transition>
        </RouterView>
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout-root {
  display: flex;
  flex-direction: column;
  height: 100%;
}

/* ===== 一体化标题栏（48px，Mica，可拖拽） ===== */
.titlebar {
  height: 48px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  user-select: none;
}
.titlebar-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 16px;
  min-width: 0;
  cursor: default;
}
.logo-mark-sm {
  width: 20px;
  height: 20px;
  border-radius: 5px;
}
.logo-mark {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--win-accent);
  color: #fff;
  box-shadow: var(--win-shadow-control);
  flex-shrink: 0;
}
.titlebar-title {
  font-size: 14px;
  font-weight: 400;
  color: var(--win-text-primary);
  white-space: nowrap;
}
.titlebar-controls {
  display: flex;
  height: 100%;
  margin-left: auto;
  align-items: center;
}
.tb-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 32px;
  border: none;
  background: transparent;
  color: var(--win-text-primary);
  cursor: pointer;
  transition: background var(--win-duration-fast) var(--win-ease);
}
.tb-btn:hover { background: var(--win-control-hover); }
.tb-btn:active { background: var(--win-control-pressed); }
.tb-close:hover { background: #C42B1C; color: #fff; }
.tb-close:active { background: #A3231A; color: #fff; }

/* ===== 主体 ===== */
.layout-body {
  flex: 1;
  display: flex;
  min-height: 0;
}

/* ===== 侧边栏（Win11 NavigationView，Mica 背景层） ===== */
.sidebar {
  width: 186.67px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  transition: width var(--win-duration-normal) var(--win-ease);
  overflow: hidden;
  /* 与内容区仅靠 Mica 层次区分，无硬分割线；加一层极淡的色调微差 */
  background-image:
    linear-gradient(180deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.01)),
    var(--win-mica);
}
html.dark .sidebar {
  background-image:
    linear-gradient(180deg, rgba(255, 255, 255, 0.015), rgba(255, 255, 255, 0.005)),
    var(--win-mica);
}
.sidebar.collapsed {
  width: 48px;
}
.sidebar.collapsed .nav-item {
  padding: 0 6px;
  justify-content: center;
}
.sidebar.collapsed .nav-item.is-active::before {
  left: -8px; /* 竖条仍贴侧边栏左边缘，与内边距无关 */
}

/* ===== 导航列表 ===== */
.nav-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 8px 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  scrollbar-width: thin;
  position: relative;
}
.nav-item.foot-item {
  border: none;
  background: transparent;
  font-family: inherit;
  width: 100%;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  height: 40px;
  padding: 0 12px 0 16px;
  overflow: visible;
  border-radius: var(--win-radius-control);
  font-size: 14px;
  font-weight: 400;
  color: var(--win-text-secondary);
  text-decoration: none;
  -webkit-tap-highlight-color: transparent;
  cursor: pointer;
  outline: none;
  white-space: nowrap;
  transition:
    background var(--win-duration-fast) var(--win-ease),
    color var(--win-duration-fast) var(--win-ease);
}
.nav-item:hover {
  background: var(--win-control-hover);
  color: var(--win-text-primary);
}
.nav-item:active {
  background: var(--win-control-pressed);
}

.nav-icon-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  color: currentColor;
}

/* 选中态：左侧 4px 强调色竖条（Selection Indicator）+ 悬停底色 */
.nav-item.is-active {
  background: var(--win-control-hover);
  color: var(--win-text-primary);
  font-weight: 600;
}
.nav-item.is-active::before {
  content: "";
  position: absolute;
  left: -8px;
  top: 0;
  bottom: 0;
  width: 4px;
  border-radius: 0 2px 2px 0;
  background: var(--win-accent);
}
.nav-item.is-active .nav-icon-wrap {
  color: var(--win-accent);
}

/* ===== 底部按钮区 ===== */
.sidebar-footer {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px;
  flex-shrink: 0;
}
.foot-item {
  color: var(--win-text-secondary);
  font-size: 14px;
}
.foot-item:hover {
  color: var(--win-text-primary);
}

/* ===== 主内容区（Mica 基底，卡片白色叠加） ===== */
.main-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  min-width: 0;
}
</style>
