import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { setupRipple } from './composables/useRipple'
import { vTooltip } from './composables/vTooltip'
import './main.css'
import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN.json'
import en from './locales/en.json'

// 强制动效豁免：系统关闭动画（prefers-reduced-motion: reduce）时，
// 给 <html> 加 .no-reduce，让 main.css 的 reduce 压平规则跳过，应用内动效照常。
// 必须在挂载前执行（CSS 选择器 html:not(.no-reduce) 实时生效）。
if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  document.documentElement.classList.add('no-reduce')
}

const app = createApp(App)
app.directive('tooltip', vTooltip)

const i18n = createI18n({
  legacy: false,           // Composition API 风格
  locale: 'zh-CN',         // 默认语言；未来从 config.gui.language 读
  fallbackLocale: 'en',
  messages: { 'zh-CN': zhCN, en }
})

app.use(i18n).use(router).mount('#root')

// 全局 ripple 注入（在挂载后启动，避免拦截 setup 阶段的事件）
setupRipple()

// Win11 overlay 滚动条：滚动时给容器加 .is-scrolling（停止 1s 后移除），
// 让滚动条短暂显现。scroll 不冒泡，需在捕获阶段监听。
let scrollHideTimer: ReturnType<typeof setTimeout> | null = null
document.addEventListener(
  'scroll',
  (e) => {
    const el = (e.target === document ? document.scrollingElement : e.target) as HTMLElement | null
    if (!el) return
    el.classList?.add('is-scrolling')
    if (scrollHideTimer) clearTimeout(scrollHideTimer)
    scrollHideTimer = setTimeout(() => el.classList?.remove('is-scrolling'), 1000)
  },
  true,
)
