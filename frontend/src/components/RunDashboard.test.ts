/** H17 (2026-08-26) RunDashboard 容器测试 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RunDashboard from './RunDashboard.vue'
import CountUp from './CountUp.vue'

describe('RunDashboard 仪表盘容器', () => {
  it('默认渲染：4 个聚合卡 + LaneView + DiscoveryFeed', () => {
    const wrapper = mount(RunDashboard, {
      props: { running: false, concurrency: 4 },
    })
    // 4 个聚合卡
    const cards = wrapper.findAll('.rd-card')
    expect(cards.length).toBe(4)
    expect(cards[0].text()).toContain('已完成')
    expect(cards[1].text()).toContain('ETA')
    expect(cards[2].text()).toContain('本会话输入')
    expect(cards[3].text()).toContain('本会话输出')
    // LaneView 子组件
    expect(wrapper.find('.lane-view').exists()).toBe(true)
    // DiscoveryFeed 子组件
    expect(wrapper.find('.discovery-feed').exists()).toBe(true)
  })

  it('数据透传：progress / sessionTokens 正确传到 CountUp 子组件', () => {
    const wrapper = mount(RunDashboard, {
      props: {
        running: true,
        progress: { current: 42, total: 100, eta: '5m30s' },
        sessionTokens: { input: 12345, output: 6789 },
      },
    })
    // 4 张聚合卡，但 ETA 用纯文本（不是 CountUp），所以只有 3 个 CountUp
    const countUps = wrapper.findAllComponents(CountUp)
    expect(countUps.length).toBe(3)
    expect(countUps[0].props('value')).toBe(42)        // 已完成
    expect(countUps[1].props('value')).toBe(12345)     // 输入 tokens
    expect(countUps[2].props('value')).toBe(6789)      // 输出 tokens
    // ETA 文本（纯文本 span）
    const cards = wrapper.findAll('.rd-card')
    expect(cards[1].text()).toContain('5m30s')
  })
})
