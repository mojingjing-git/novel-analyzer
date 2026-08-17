/**
 * 全局 Ripple 涟漪注入器（Win11 标志微交互）
 *
 * 通过事件代理给所有匹配选择器的可交互元素注入点击涟漪：
 *   .glass-button、.glass-pill、.glass-button-primary、.glass-button-danger、
 *   nav-item、.switch .slider
 *
 * 实现：在 mousedown 时获取指针相对按钮位置，注入 <span class="ripple">，
 * 用 CSS keyframes 触发 600ms scale(0→2.6) + 透明度 0.20→0 的扩散动画。
 *
 * 设计取舍：用全局事件代理而非 Vue 指令，避免在 30+ 个按钮上手工 v-ripple；
 * 缺点是动态新增按钮需重新调用 setupRipple()（一般 AppLayout 一处初始化足够）。
 */

const RIPPLE_TARGETS = [
  '.glass-button',
  '.glass-pill',
  '.glass-button-primary',
  '.glass-button-danger',
  '.op-btn',
  '.nav-item',
]

let initialized = false

function findRippleHost(el: HTMLElement | null): HTMLElement | null {
  let cur: HTMLElement | null = el
  while (cur && cur !== document.body) {
    for (const sel of RIPPLE_TARGETS) {
      if (cur.matches?.(sel)) return cur
    }
    cur = cur.parentElement
  }
  return null
}

function createRipple(host: HTMLElement, e: MouseEvent) {
  // 禁用态按钮不接受 ripple
  if ((host as HTMLButtonElement).disabled) return

  const rect = host.getBoundingClientRect()
  // 直径取按钮宽高较大者 × 2：保证完整覆盖按钮但不溢出过大
  const size = Math.max(rect.width, rect.height) * 2
  const x = e.clientX - rect.left - size / 2
  const y = e.clientY - rect.top - size / 2

  const span = document.createElement('span')
  span.className = 'ripple'
  span.style.width = `${size}px`
  span.style.height = `${size}px`
  span.style.left = `${x}px`
  span.style.top = `${y}px`

  host.appendChild(span)
  // 700ms 后移除（CSS 动画 600ms，留 100ms 余量防误删）
  setTimeout(() => {
    if (span.parentNode) span.parentNode.removeChild(span)
  }, 700)
}

function onMouseDown(e: MouseEvent) {
  // 只响应鼠标主键（左键）
  if (e.button !== 0) return
  const host = findRippleHost(e.target as HTMLElement)
  if (host) createRipple(host, e)
}

/**
 * 初始化全局 ripple 注入器（幂等，多次调用仅生效一次）
 * 在 main.ts 启动时或 onMounted 顶层调用
 */
export function setupRipple() {
  if (initialized) return
  initialized = true
  document.addEventListener('mousedown', onMouseDown, true)
  if (typeof window !== 'undefined' && 'ontouchstart' in window) {
    document.addEventListener('touchstart', (e: TouchEvent) => {
      const t = e.touches[0]
      if (t) onMouseDown(new MouseEvent('mousedown', { clientX: t.clientX, clientY: t.clientY, button: 0 }))
    }, true)
  }
}
