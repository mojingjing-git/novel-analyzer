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
