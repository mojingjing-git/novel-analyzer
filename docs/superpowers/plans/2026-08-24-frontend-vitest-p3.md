# 前端 vitest 基建 + P3 尾巴清扫实施计划（2026-08-24 第三批）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为前端建立 vitest 自动化测试基建并用守护性测试锁住本会话修复的竞态守卫/清理逻辑；顺手清零审计清单上的全部前端 P3 残留；把「切书守卫三模式」规范与 useBookScope 缓做决策写入 agent.md。

**Architecture:** 独立 vitest.config.ts（不动 vite.config.ts 构建链路）；纯逻辑模块（useLogStore/useProgressSocket）用 `vi.resetModules()+动态导入` 处理模块级单例；组件级竞态守卫用 @vue/test-utils 挂载 TimelinePage 作代表模式测试；client.ts 用 fetch mock 直接测。

**Tech Stack:** vitest@latest + @vue/test-utils + jsdom（均为 devDependency）；Node 24 自带 TS 类型剥离但测试统一走 vitest 运行器。

## Global Constraints

- 本批**后端零改动**；所有验证命令在 `frontend/` 目录下执行
- 每个任务完成后必须双绿：`npx vitest run` 全过 **且** `npm run build` 成功（vue-tsc 会类型检查测试文件，测试代码必须类型干净——显式 `import { describe, it, expect, vi } from 'vitest'`，不用 globals）
- 禁止打印仓库根 config.json；不动行尾符；commit 中文一行式只 add 列出文件
- 不引入 Pinia/状态库、不做 useBookScope 重构（决策见 Task V8）
- 测试不得依赖执行顺序；模块级单例的隔离手段统一为 `vi.resetModules()` + 动态 `await import()`

---

### Task V1: vitest 基建

**Files:**
- Modify: `frontend/package.json`（devDependencies + scripts.test）
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/sanity.test.ts`（冒烟，后续可删）

**Interfaces:**
- Produces: `npm test`（=vitest run）；`vitest.config.ts` 含 vue 插件与 jsdom 环境，供后续任务直接加 `*.test.ts`

- [ ] **Step 1: 安装依赖**

Run: `cd frontend && npm i -D vitest @vue/test-utils jsdom`

- [ ] **Step 2: 配置**

新建 `frontend/vitest.config.ts`：

```typescript
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
})
```

package.json scripts 加：

```json
"test": "vitest run"
```

- [ ] **Step 3: 冒烟测试**

新建 `frontend/src/sanity.test.ts`：

```typescript
import { describe, it, expect } from 'vitest'

describe('vitest 冒烟', () => {
  it('运行环境具备 jsdom 能力', () => {
    expect(typeof window).toBe('object')
    expect(typeof window.localStorage).toBe('object')
  })
})
```

- [ ] **Step 4: 双绿验证**

Run: `cd frontend && npm test && npm run build`
Expected: 冒烟 1 passed；vue-tsc/vite 构建成功（证明测试文件不影响构建类型检查）

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/src/sanity.test.ts
git commit -m "test(frontend): 引入vitest基建(jsdom环境+独立配置+test脚本)"
```

---

### Task V2: markdown 测试迁移进 vitest

**Files:**
- Rewrite: `frontend/src/utils/markdown.test.ts`（保留全部既有断言，改写为 vitest 风格）

**背景**：现为自运行 console 断言脚本（靠 npx tsx 执行），不在任何测试命令覆盖内。迁移后进入 `npm test`，并删除对 tsx 的依赖路径。

- [ ] **Step 1: 重写测试文件**

整文件替换为（断言集合与现文件一一对应，含 A2 的链接保护用例）：

```typescript
/** Markdown 渲染器测试（vitest 版，2026-08-24 自运行脚本迁移） */
import { describe, it, expect } from 'vitest'
import { renderMarkdown } from './markdown'

describe('markdown 渲染器', () => {
  it('标题', () => {
    const r = renderMarkdown('# H1\n## H2\n### H3')
    expect(r).toContain('<h1 class="md-h1">H1</h1>')
    expect(r).toContain('<h2 class="md-h2">H2</h2>')
    expect(r).toContain('<h3 class="md-h3">H3</h3>')
  })

  it('粗体/斜体/行内代码', () => {
    const r = renderMarkdown('**bold** *italic* `code`')
    expect(r).toContain('<strong>bold</strong>')
    expect(r).toContain('<em>italic</em>')
    expect(r).toContain('<code class="md-code">code</code>')
  })

  it('XSS 转义', () => {
    const r = renderMarkdown('<script>alert(1)</script>')
    expect(r).toContain('&lt;script&gt;')
    expect(r).not.toContain('<script>')
  })

  it('空文本返回空串', () => {
    expect(renderMarkdown('')).toBe('')
  })

  it('段落合并与换行', () => {
    const r = renderMarkdown('第一段\n第二段')
    expect(r).toContain('<p class="md-p">')
    expect(r).toContain('<br/>')
  })

  it('链接 href 不被强调正则污染（P2 2026-08-24）', () => {
    const r = renderMarkdown('[doc](https://example.com/wiki/a_b_c)')
    expect(r).not.toContain('<em>')
    expect(r).not.toContain('<strong>')
    expect(r).not.toContain('<del>')
    expect(r).toContain('href="https://example.com/wiki/a_b_c"')
  })

  it('可见文本强调仍生效且 URL 内下划线不被污染', () => {
    const r = renderMarkdown('__init__ 与 [x](https://a.io/p__q)')
    expect(r).toContain('<strong>init</strong>')
    expect(r).toContain('href="https://a.io/p__q"')
  })
})
```

- [ ] **Step 2: 双绿验证**

Run: `cd frontend && npm test && npm run build`
Expected: 全部通过（含迁移用例）

- [ ] **Step 3: Commit**

```bash
git add frontend/src/utils/markdown.test.ts
git commit -m "test(markdown): 自运行脚本迁移进vitest"
```

---

### Task V3: useLogStore 守护测试

**Files:**
- Create: `frontend/src/composables/useLogStore.test.ts`

**背景**：模块级单例 + import 时从 localStorage 初始化 + 500ms 节流持久化。P2 批修过「坏 id 污染 nextId」，此处用测试锁死该行为及容量/迁移/节流语义。

**隔离手法**：每个用例 `vi.resetModules()` + 动态 `await import('./useLogStore')` 取全新模块图；localStorage 在 seed 时先写再导入。

- [ ] **Step 1: 写测试**

新建 `frontend/src/composables/useLogStore.test.ts`：

```typescript
/** useLogStore 守护测试：单例隔离用 vi.resetModules + 动态导入 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const KEY = 'novel-analyzer-logs'

function seed(entries: unknown[]) {
  window.localStorage.setItem(KEY, JSON.stringify(entries))
}

async function freshStore() {
  vi.resetModules()
  const mod = await import('./useLogStore')
  return mod.useLogStore()
}

beforeEach(() => {
  window.localStorage.clear()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useLogStore', () => {
  it('坏条目被过滤且 nextId 不被污染（P2 修复锁定）', async () => {
    seed([{ id: 1, text: '好条目' }, { id: 'bad', text: 'x' }, { text: '无id' }])
    const store = await freshStore()
    expect(store.logs.value).toHaveLength(1)
    store.add('新日志')
    expect(store.logs.value.at(-1)!.id).toBe(2)
    expect(store.logs.value.at(-1)!.text).toBe('新日志')
  })

  it('加载即裁剪到 2000 条上限', async () => {
    seed(Array.from({ length: 2050 }, (_, i) => ({ id: i + 1, text: `t${i}` })))
    const store = await freshStore()
    expect(store.logs.value).toHaveLength(2000)
    expect(store.logs.value[0].id).toBe(51)
  })

  it('migrateSource 给旧 Python 日志补 source', async () => {
    seed([{ id: 1, text: '2026-08-07 10:00:00 [INFO] backend.core.pipeline: xxx' }])
    const store = await freshStore()
    expect(store.logs.value[0].source).toBe('python')
  })

  it('add 超限裁剪 + 节流持久化合并写入', async () => {
    seed(Array.from({ length: 1999 }, (_, i) => ({ id: i + 1, text: `t${i}` })))
    const store = await freshStore()
    store.add('第一条')
    store.add('第二条')
    expect(store.logs.value).toHaveLength(2000)
    vi.advanceTimersByTime(500)
    const saved = JSON.parse(window.localStorage.getItem(KEY)!)
    expect(saved).toHaveLength(2000)
    expect(saved.at(-1).text).toBe('第二条')
  })

  it('clear 清空内存并移除存储键', async () => {
    seed([{ id: 1, text: 'a' }])
    const store = await freshStore()
    store.clear()
    expect(store.logs.value).toHaveLength(0)
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('filter/removeByCategory 按 category 生效', async () => {
    const store = await freshStore()
    store.add('分析日志', 'info', 'analysis')
    store.add('总结日志', 'info', 'summary')
    expect(store.filter('summary')).toHaveLength(1)
    store.removeByCategory('analysis')
    expect(store.logs.value).toHaveLength(1)
    expect(store.logs.value[0].category).toBe('summary')
  })
})
```

- [ ] **Step 2: 双绿验证**

Run: `cd frontend && npm test && npm run build`
Expected: 全过（若「节流合并」用例因实现细节失败——如 watch deep 触发时机差异——允许按实际行为调整断言粒度为「500ms 后存储已写入且条数为最新」，但不得删用例）

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/useLogStore.test.ts
git commit -m "test(log-store): 单例隔离守护测试(过滤/裁剪/迁移/节流)"
```

---

### Task V4: useProgressSocket 守护测试

**Files:**
- Create: `frontend/src/api/useProgressSocket.test.ts`

**背景**：单例 WS + pub/sub + 指数退避重连（1s 起 ×2 封顶 10s）+ manualClose 防孤儿 + ping 过滤。P2 审计确认 manualClose 有粘滞缺陷但生产无调用方——测试同时锁定现有语义（含粘滞行为本身），防止未来接线时无护栏。

**隔离手法**：同 V3（resetModules + 动态导入）；全局 `WebSocket` 用 FakeWebSocket 替身；退避用假时钟推进。

- [ ] **Step 1: 写测试**

新建 `frontend/src/api/useProgressSocket.test.ts`：

```typescript
/** useProgressSocket 守护测试：FakeWebSocket + 假时钟 + resetModules 隔离 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  url: string
  readyState = 0
  sent: string[] = []
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  constructor(url: string) { this.url = url; FakeWebSocket.instances.push(this) }
  send(data: string) { this.sent.push(data) }
  close() { this.readyState = 3; this.onclose?.() }
  open() { this.readyState = 1; this.onopen?.() }
  receive(obj: unknown) { this.onmessage?.({ data: JSON.stringify(obj) }) }
  drop() { this.onclose?.() }
}

async function freshModule() {
  vi.resetModules()
  return await import('./useProgressSocket')
}

async function inComponent<T>(fn: (m: typeof import('./useProgressSocket')) => T): Promise<T> {
  let out!: T
  mount({ setup() { out = fn(mCurrent!); return () => '<div/>' } })
  return out
}

let mCurrent: typeof import('./useProgressSocket') | null = null

beforeEach(async () => {
  vi.useFakeTimers()
  vi.stubGlobal('WebSocket', FakeWebSocket)
  FakeWebSocket.instances = []
  mCurrent = await freshModule()
})

afterEach(() => {
  mCurrent?.closeProgressSocket()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  mCurrent = null
})

describe('useProgressSocket', () => {
  it('init 单例：两次初始化只建一条连接', async () => {
    const m = mCurrent!
    m.initProgressSocket()
    m.initProgressSocket()
    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('非 ping 消息分发给订阅者，ping 被静默丢弃', async () => {
    const m = mCurrent!
    const seen: string[] = []
    await inComponent(m2 => m2.useProgressSocket(msg => seen.push(msg.type)))
    FakeWebSocket.instances[0].open()
    FakeWebSocket.instances[0].receive({ type: 'ping', payload: {} })
    FakeWebSocket.instances[0].receive({ type: 'block_done', payload: {} })
    expect(seen).toEqual(['block_done'])
  })

  it('订阅者抛异常不影响其他订阅者', async () => {
    const m = mCurrent!
    const seen: string[] = []
    await inComponent(m2 => {
      m2.useProgressSocket(() => { throw new Error('炸了') })
      m2.useProgressSocket(msg => seen.push(msg.type))
    })
    FakeWebSocket.instances[0].open()
    FakeWebSocket.instances[0].receive({ type: 'progress', payload: {} })
    expect(seen).toEqual(['progress'])
  })

  it('自然掉线按指数退避重连：1s→2s，成功 open 后归零', async () => {
    const m = mCurrent!
    m.initProgressSocket()
    FakeWebSocket.instances[0].drop()                    // retry=0 → 1s
    await vi.advanceTimersByTimeAsync(999)
    expect(FakeWebSocket.instances).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeWebSocket.instances).toHaveLength(2)
    FakeWebSocket.instances[1].drop()                    // retry=1 → 2s
    await vi.advanceTimersByTimeAsync(1999)
    expect(FakeWebSocket.instances).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    FakeWebSocket.instances[2].open()                    // 成功 → retry 归零
    FakeWebSocket.instances[2].drop()
    await vi.advanceTimersByTimeAsync(999)
    expect(FakeWebSocket.instances).toHaveLength(3)      // 又是 1s 档
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeWebSocket.instances).toHaveLength(4)
  })

  it('重连延迟封顶 10s', async () => {
    const m = mCurrent!
    m.initProgressSocket()
    for (let i = 0; i < 6; i++) {
      FakeWebSocket.instances.at(-1)!.drop()
      await vi.advanceTimersByTimeAsync(10_000)
    }
    // 第 7 次 drop 后延迟应停在 10s：advance 9.9s 不出新连接
    FakeWebSocket.instances.at(-1)!.drop()
    await vi.advanceTimersByTimeAsync(9_900)
    const n = FakeWebSocket.instances.length
    await vi.advanceTimersByTimeAsync(100)
    expect(FakeWebSocket.instances.length).toBe(n + 1)
  })

  it('manualClose 粘滞：主动关闭后的自然掉线不再重连（现有语义锁定）', async () => {
    const m = mCurrent!
    m.initProgressSocket()
    m.closeProgressSocket()          // sharedWs 已置 null 且 manualClose=true
    FakeWebSocket.instances[0].drop() // close() 触发的 onclose
    await vi.advanceTimersByTimeAsync(60_000)
    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('组件卸载自动移除订阅者', async () => {
    const m = mCurrent!
    const seen: string[] = []
    const w = mount({ setup() { m.useProgressSocket(msg => seen.push(msg.type)); return () => '<div/>' } })
    FakeWebSocket.instances[0].open()
    w.unmount()
    FakeWebSocket.instances[0].receive({ type: 'log', payload: {} })
    expect(seen).toEqual([])
  })
})
```

> 执行者备注：①`inComponent` 里引用 `mCurrent` 是为了让挂载发生在 beforeEach 导入的新模块图内；若 lint 抱怨可改为传参形式。②若 `mount` 的渲染函数签名报类型错，用 `template: '<div />' '` 形式等价替换。③「manualClose 粘滞」用例是**现状锁定**而非期望行为——未来若修粘滞缺陷，此用例应同步反转断言。

- [ ] **Step 2: 双绿验证**

Run: `cd frontend && npm test && npm run build`
Expected: 全过（若退避时序边界差 1ms 导致 flaky，允许把 999/1 推进改成整段 advance 到目标时刻并断言实例数增量）

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/useProgressSocket.test.ts
git commit -m "test(progress-socket): 单例/分发/退避/manualClose/卸载清理守护测试"
```

---

### Task V5: TimelinePage 竞态守卫代表测试

**Files:**
- Create: `frontend/src/pages/TimelinePage.test.ts`

**背景**：六页切书守卫中 TimelinePage 最简单（watch → 单请求 → 双数组赋值），用它作**组件级代表测试**锁定「慢的旧书响应不得覆盖新书数据」这一模式不变量。其余页面的守卫结构与之同构。

**手法**：vi.mock 掉 `../api/client` 与 `../components/BookSelector.vue`（stub 成可控发射器），用受控 Promise 制造「A 慢 B 快」时序。

- [ ] **Step 1: 写测试**

新建 `frontend/src/pages/TimelinePage.test.ts`：

```typescript
/** 切书竞态守卫代表测试：慢的旧书响应不得覆盖新书时间线 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../../src/api/client', async () => ({
  api: {
    getForeshadowCategories: vi.fn(async () => ({ defs: [], kept: [] })),
    getTimeline: vi.fn(),
  },
}))
vi.mock('../components/BookSelector.vue', () => ({
  name: 'BookSelector',
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template: `<select :value="modelValue" data-test="selector"
    @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)"></select>`,
}))

import { api } from '../api/client'
import BookSelector from '../components/BookSelector.vue'
import TimelinePage from './TimelinePage.vue'

const mockedApi = vi.mocked(api)

function pick(wrapper: ReturnType<typeof mount>, bookId: string) {
  wrapper.findComponent(BookSelector).vm.$emit('update:modelValue', bookId)
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedApi.getForeshadowCategories.mockResolvedValue({ defs: [], kept: [] })
})

describe('TimelinePage 切书守卫', () => {
  it('慢的 A 书响应不得覆盖已切换的 B 书数据', async () => {
    let resolveA!: (v: unknown) => void
    mockedApi.getTimeline.mockImplementation((id: string) => {
      if (id === 'A') {
        return new Promise(resolve => { resolveA = resolve })
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
```

> 执行者备注：①mock 路径以 TimelinePage.vue 实际 import 相径为准（`../api/client` 与 `../components/BookSelector.vue` 从 pages/ 出发应为 `../../src/...` 或直接相对 test 文件位置调整——vi.mock 的路径匹配目标是**被测模块的 import 描述符**，放 test 于 pages/ 同目录用 `../api/client` 即可，上面模板里多写的 `../../src` 若导致未命中请以实际为准统一）。②findComponent 匹配 stub 组件用导入的 BookSelector 引用；若 stub 后 findComponent 找不到，退化为 `wrapper.find('[data-test="selector"]')` + `trigger('change')` 方案。③断言基于 wrapper.text()，与模板具体 class 解耦。

- [ ] **Step 2: 双绿验证**

Run: `cd frontend && npm test && npm run build`
Expected: 2 个用例全过（第一个用例若在修复前的代码上跑应当 FAIL——可用 `git stash` 临时验证 RED 再恢复，报告注明）

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/TimelinePage.test.ts
git commit -m "test(timeline): 切书竞态守卫组件级代表测试"
```

---

### Task V6: client.ts 非 JSON 200 显式抛错 + request 行为测试

**Files:**
- Modify: `frontend/src/api/client.ts`（request() :275-277 区域）
- Create: `frontend/src/api/client.test.ts`

**背景**：ok 但 body 非 JSON 时 `(data ?? {}) as T` 静默返回 `{}`——SettingsPage 曾因此出现 `config.api.base_url` TypeError 白屏链路。改为显式抛错（带 status）；**空 body 保持宽容**（为将来 204 类端点留余地）。

- [ ] **Step 1: 写失败测试**

新建 `frontend/src/api/client.test.ts`：

```typescript
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm test`
Expected: 「非 JSON 抛错」用例 FAIL（当前静默返回 {}）

- [ ] **Step 3: 实现**

request() 的 `if (!res.ok) {...}` 之后、`return (data ?? {}) as T` 之前插入：

```typescript
  // P3 收口（2026-08-24）：ok 但 body 非 JSON 此前静默返回 {}，
  // 上游在深层字段上 TypeError 白屏且无从排查；显式失败走统一错误提示。
  // 空 body 仍宽容返回 {}（为 204 类端点留余地）。
  if (data === null && text) {
    const err = new Error(`响应不是有效 JSON (${res.status})`)
    ;(err as Error & { status?: number }).status = res.status
    throw err
  }
```

- [ ] **Step 4: 双绿验证**

Run: `cd frontend && npm test && npm run build`
Expected: 全过（注意全量跑一遍确认没有其他页面测试依赖旧的静默 {} 行为）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "fix(client): 非JSON的200响应显式抛错不再静默返回空对象"
```

---

### Task V7: 两处 P3 状态残留小修

**Files:**
- Modify: `frontend/src/pages/SummaryPage.vue`（viewAggFile catch 补 bid 守卫）
- Modify: `frontend/src/pages/GraphPage.vue`（loadData 空 bid 早退前清 loading）

- [ ] **Step 1: viewAggFile** catch 改为：

```typescript
  } catch (e) {
    if (bid !== bookId.value) return   // 已切书：过期失败的错误不污染新书
    aggError.value = (e as Error).message
  }
```

- [ ] **Step 2: GraphPage loadData** 开头改为：

```typescript
async function loadData() {
  const bid = bookId.value
  if (!bid) {
    // P3：切回占位项时清掉上一本书遗留的加载态，否则一直转圈到选中新书
    loading.value = false
    return
  }
  loading.value = true
  ...（原样）...
```

- [ ] **Step 3: 双绿验证 + Commit**

Run: `cd frontend && npm test && npm run build`
```bash
git add frontend/src/pages/SummaryPage.vue frontend/src/pages/GraphPage.vue
git commit -m "fix(frontend): 过期失败不污染aggError+占位项清除loading残留"
```

---

### Task V8: 文档收口（三模式规范 + useBookScope 决策）

**Files:**
- Modify: `agent.md`（§6.6 补三条守卫模式规范；§10 新增 10.16 条目）
- Modify: `CHANGELOG.md`

- [ ] **Step 1: §6.6 前端关键模式追加一条（放在现有列表末尾）**

```markdown
- **切书请求守卫三种正确写法（2026-08-24 审计后确立，新增页面必须选用其一）**：
  ①seq 计数器型——适合同一资源反复加载（ChapterDetailPanel/CharacterCardPage.viewCard）；
  ②bookId 快照比对型——适合 watch(bookId) 触发、且 catch 分支也会写状态的页面（Timeline/Graph/Map/Summary.loadReport）；
  ③finally 条件复位型——loading 等互斥标志必须 `if (seq === requestSeq)` 才翻转。
  共同底线：catch 分支同样要守卫；早退分支也要 bump seq；useBookScope 统一上下文方案已评估、决定暂缓（见 10.16），新页面手写守卫时参照本节。
```

- [ ] **Step 2: §10 新增条目（编号续 10.16）**

```markdown
### 10.16 2026-08-24 前端 vitest 基建 + P3 清扫
- 引入 vitest/jsdom/@vue/test-utils 与独立 vitest.config.ts；markdown 自运行脚本迁入；useLogStore/useProgressSocket 守护测试（含退避时序、manualClose 现状锁定）；TimelinePage 竞态守卫组件级代表测试；client.ts 非 JSON 200 显式抛错；viewAggFile/GraphPage 两处状态残留小修
- **原因**：竞态守卫类修复此前无自动化护栏，误删即静默回归
- **决策**：useBookScope 统一上下文评估后暂缓——6 处竞态已修完且新增页面低频，规范写入 §6.6 代替；待第 13 个页面落地时再抽 composable 迁移
- **未动**：OpenAPI 生成 client.ts（规模不够）、ECharts 再加固（已稳定）
```

CHANGELOG 追加一行风格随现有文件。

- [ ] **Step 3: 最终验证 + Commit**

Run: `cd frontend && npm test && npm run build`；后端不动故 pytest 免跑（如跑亦应 227 passed）
```bash
git add agent.md CHANGELOG.md
git commit -m "docs(agent): 切书守卫三模式入§6.6+useBookScope缓做决策记录"
```

---

## Self-Review 结论

1. **覆盖核对**：讨论承诺的三项全部落位——V1-V5 测试基建+守护测试、V6-V7 P3 清扫、V8 useBookScope 决策文档化；明确排除项（OpenAPI/ECharts 加固）在 V8 条目中记录。
2. **占位符扫描**：V4/V5 的执行者备注是路径/断言适配指引，均给出等价替代方案；无 TBD。
3. **一致性**：`seg()` 未触碰；`_sleep` 等 P1/P2 符号无交叉；测试文件命名统一 `*.test.ts` 与 vitest include 匹配；V4 的 FakeWebSocket.close 触发 onclose 与真实 WS 语义一致（manualClose 用例依赖此点）。
