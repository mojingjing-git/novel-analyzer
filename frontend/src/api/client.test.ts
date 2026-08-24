/** request() 行为契约：JSON 解析/错误提取/非 JSON 显式失败 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { api } from './client'

function mockFetch(body: string, ok = true, status = 200, statusText = 'OK') {
  const fn = vi.fn(async () => ({
    ok, status, statusText,
    text: async () => body,
  }))
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => vi.unstubAllGlobals())

describe('client.request', () => {
  it('合法 JSON 正常解析', async () => {
    mockFetch('{"a":1}')
    expect(await api.health()).toEqual({ a: 1 })
  })

  it('空 body 宽容返回空对象', async () => {
    mockFetch('', true, 204)
    expect(await api.health()).toEqual({})
  })

  it('ok 但 body 非 JSON 必须显式抛错（此前静默返回 {} 致下游 TypeError 白屏）', async () => {
    mockFetch('<html>Bad Gateway</html>')
    await expect(api.health()).rejects.toMatchObject({ status: 200 })
  })

  it('非 ok 时优先取 detail 字段作为错误消息并附加 status', async () => {
    mockFetch('{"detail":"书目不存在"}', false, 404, 'Not Found')
    await expect(api.getBookReport('某书')).rejects.toMatchObject({
      message: '书目不存在',
      status: 404,
    })
  })
})
