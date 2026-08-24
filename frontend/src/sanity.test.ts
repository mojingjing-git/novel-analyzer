import { describe, it, expect } from 'vitest'

describe('vitest 冒烟', () => {
  it('运行环境具备 jsdom 能力', () => {
    expect(typeof window).toBe('object')
    expect(typeof window.localStorage).toBe('object')
  })
})
