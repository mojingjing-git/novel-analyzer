/** 切书竞态守卫代表测试：慢的旧书响应不得覆盖新书时间线 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../api/client', async () => ({
  api: {
    getForeshadowCategories: vi.fn(async () => ({ defs: [], kept: [] })),
    getTimeline: vi.fn(),
  },
}))
// stub 成可控发射器：真实组件会自请求书目列表，测试只关心 v-model 转发
vi.mock('../components/BookSelector.vue', () => ({
  default: {
    name: 'BookSelector',
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template: `<select :value="modelValue" data-test="selector"
      @change="$emit('update:modelValue', $event.target.value)"></select>`,
  },
}))

import { api } from '../api/client'
import BookSelector from '../components/BookSelector.vue'
import TimelinePage from './TimelinePage.vue'

type TimelineResp = Awaited<ReturnType<typeof api.getTimeline>>

const mockedApi = vi.mocked(api)

function pick(wrapper: ReturnType<typeof mount>, bookId: string) {
  wrapper.findComponent(BookSelector).vm.$emit('update:modelValue', bookId)
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedApi.getForeshadowCategories.mockResolvedValue({
    schema_version: 1,
    fallback: 'builtin',
    defs: [],
    kept: [],
    min_importance: '低',
    min_confidence: '中',
  })
})

describe('TimelinePage 切书守卫', () => {
  it('慢的 A 书响应不得覆盖已切换的 B 书数据', async () => {
    let resolveA!: (v: TimelineResp) => void
    mockedApi.getTimeline.mockImplementation((id: string) => {
      if (id === 'A') {
        return new Promise<TimelineResp>(resolve => { resolveA = resolve })
      }
      return Promise.resolve({
        events: [{ chapter: 2, event: 'B书的关键事件', characters: '', function: '', importance: '高' }],
        foreshadows: [],
      })
    })

    const wrapper = mount(TimelinePage)
    pick(wrapper, 'A')
    await flushPromises()                       // A 挂起
    pick(wrapper, 'B')
    await flushPromises()                       // B 完成
    expect(wrapper.text()).toContain('B书的关键事件')

    resolveA({                                  // A 迟到
      events: [{ chapter: 1, event: 'A书的过期事件', characters: '', function: '' }],
      foreshadows: [],
    })
    await flushPromises()
    expect(wrapper.text()).not.toContain('A书的过期事件')
    expect(wrapper.text()).toContain('B书的关键事件')
  })

  it('正常顺序切换两本书各自渲染', async () => {
    mockedApi.getTimeline.mockImplementation((id: string) =>
      Promise.resolve({
        events: [{ chapter: 1, event: `${id}的事件`, characters: '', function: '' }],
        foreshadows: [],
      }))
    const wrapper = mount(TimelinePage)
    pick(wrapper, 'BOOK1')
    await flushPromises()
    expect(wrapper.text()).toContain('BOOK1的事件')
    pick(wrapper, 'BOOK2')
    await flushPromises()
    expect(wrapper.text()).toContain('BOOK2的事件')
    expect(wrapper.text()).not.toContain('BOOK1的事件')
  })
})
