// Win11 风格自定义 tooltip 指令
// 替代原生 HTML title 属性：
// - 正确支持 \n 换行（template 里 ':title="foo + \'\\n\'"' 会被 Vue 当字面字符处理）
// - 跨浏览器样式一致
// - 居中放置在元素上方，空间不够翻下方

import type { Directive, DirectiveBinding } from 'vue'

interface TooltipState {
  el: HTMLElement
  tip: HTMLElement
  text: string
}

let activeState: TooltipState | null = null
let showTimer: number | null = null
let hideTimer: number | null = null
const TOOLTIP_OFFSET = 8
const SHOW_DELAY_MS = 350
const HIDE_DELAY_MS = 100

function getText(binding: DirectiveBinding<string | undefined | null>): string {
  const v = binding.value
  if (v == null) return ''
  return String(v)
}

function clearTimers() {
  if (showTimer !== null) { clearTimeout(showTimer); showTimer = null }
  if (hideTimer !== null) { clearTimeout(hideTimer); hideTimer = null }
}

function positionTip(tip: HTMLElement, anchor: HTMLElement) {
  const tipRect = tip.getBoundingClientRect()
  const anchorRect = anchor.getBoundingClientRect()
  const vw = window.innerWidth
  const vh = window.innerHeight

  let top = anchorRect.top - tipRect.height - TOOLTIP_OFFSET
  let placement: 'top' | 'bottom' = 'top'
  if (top < 8) {
    top = anchorRect.bottom + TOOLTIP_OFFSET
    placement = 'bottom'
  }

  let left = anchorRect.left + anchorRect.width / 2 - tipRect.width / 2
  if (left < 8) left = 8
  if (left + tipRect.width > vw - 8) left = vw - tipRect.width - 8

  tip.style.top = `${Math.max(8, Math.min(top, vh - tipRect.height - 8))}px`
  tip.style.left = `${left}px`
  tip.dataset.placement = placement
}

function show(el: HTMLElement, text: string) {
  if (hideTimer !== null) { clearTimeout(hideTimer); hideTimer = null }
  if (!text.trim()) return
  if (showTimer !== null) clearTimeout(showTimer)

  showTimer = window.setTimeout(() => {
    // 已有 tooltip：先移除
    if (activeState) {
      activeState.tip.remove()
      activeState = null
    }

    const tip = document.createElement('div')
    tip.className = 'v-tooltip-bubble'
    tip.setAttribute('role', 'tooltip')
    // textContent 会保留 \n，再由 CSS white-space: pre-line 渲染成换行
    tip.textContent = text
    document.body.appendChild(tip)

    positionTip(tip, el)
    activeState = { el, tip, text }

    // 双 RAF：等首帧 layout 后再加 is-visible 触发过渡
    requestAnimationFrame(() => {
      requestAnimationFrame(() => tip.classList.add('is-visible'))
    })
  }, SHOW_DELAY_MS)
}

function hide() {
  if (showTimer !== null) { clearTimeout(showTimer); showTimer = null }
  if (!activeState) return
  if (hideTimer !== null) clearTimeout(hideTimer)

  hideTimer = window.setTimeout(() => {
    if (!activeState) return
    activeState.tip.classList.remove('is-visible')
    const tip = activeState.tip
    activeState = null
    setTimeout(() => tip.remove(), 180)
  }, HIDE_DELAY_MS)
}

function onEnter(this: HTMLElement, e: Event, binding: DirectiveBinding<string | undefined | null>) {
  show(this, getText(binding))
  if (e instanceof MouseEvent) e.preventDefault()
}

function onLeave() {
  hide()
}

export const vTooltip: Directive<HTMLElement, string | undefined | null> = {
  mounted(el, binding) {
    el.addEventListener('mouseenter', (e) => onEnter.call(el, e, binding))
    el.addEventListener('mouseleave', onLeave)
    el.addEventListener('focusin', (e) => onEnter.call(el, e, binding))
    el.addEventListener('focusout', onLeave)
  },
  updated(el, binding) {
    if (activeState && activeState.el === el && activeState.text !== getText(binding)) {
      activeState.tip.textContent = getText(binding)
      activeState.text = getText(binding)
    }
  },
  unmounted(el) {
    // 用 binding 引用会被 GC 回收，监听器随元素销毁
    el.removeEventListener('mouseleave', onLeave)
    el.removeEventListener('focusout', onLeave)
    if (activeState && activeState.el === el) {
      activeState.tip.remove()
      activeState = null
    }
    clearTimers()
  },
}
