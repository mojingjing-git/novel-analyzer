import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { setupRipple } from './composables/useRipple'
import './main.css'

createApp(App).use(router).mount('#root')

// 全局 ripple 注入（在挂载后启动，避免拦截 setup 阶段的事件）
setupRipple()
