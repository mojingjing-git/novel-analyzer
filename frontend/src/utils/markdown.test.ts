/**
 * Markdown 解析器测试
 */
import { renderMarkdown } from './markdown'

function assertContains(html: string, needle: string, msg: string) {
  if (!html.includes(needle)) {
    console.error(`❌ FAIL: ${msg}`)
    console.error(`   expected to contain: ${needle}`)
    console.error(`   actual: ${html}`)
    process.exit(1)
  }
}

function runTests() {
  // 标题
  let r = renderMarkdown('# H1\n## H2\n### H3')
  assertContains(r, '<h1 class="md-h1">H1</h1>', 'H1')
  assertContains(r, '<h2 class="md-h2">H2</h2>', 'H2')
  assertContains(r, '<h3 class="md-h3">H3</h3>', 'H3')
  console.log('✅ 标题')

  // 粗体 + 斜体 + 行内代码
  r = renderMarkdown('**bold** *italic* `code`')
  assertContains(r, '<strong>bold</strong>', 'bold')
  assertContains(r, '<em>italic</em>', 'italic')
  assertContains(r, '<code class="md-code">code</code>', 'inline code')
  console.log('✅ 行内格式')

  // 列表
  r = renderMarkdown('- a\n- b\n- c')
  assertContains(r, '<ul class="md-ul">', 'ul')
  assertContains(r, '<li>a</li>', 'li a')
  assertContains(r, '<li>c</li>', 'li c')
  r = renderMarkdown('1. one\n2. two')
  assertContains(r, '<ol class="md-ol">', 'ol')
  assertContains(r, '<li>one</li>', 'ol 1')
  console.log('✅ 列表')

  // 代码块
  r = renderMarkdown('```python\nprint("hi")\n```')
  assertContains(r, '<pre class="md-pre">', 'pre')
  assertContains(r, 'language-python', 'lang')
  assertContains(r, 'print(&quot;hi&quot;)', 'escaped')
  console.log('✅ 代码块')

  // 引用
  r = renderMarkdown('> quote here')
  assertContains(r, '<blockquote class="md-quote">', 'blockquote')
  assertContains(r, 'quote here', 'quote text')
  console.log('✅ 引用')

  // 链接
  r = renderMarkdown('[百度](https://baidu.com)')
  assertContains(r, '<a href="https://baidu.com"', 'link href')
  assertContains(r, 'target="_blank"', 'link target')
  assertContains(r, '>百度</a>', 'link text')
  // 危险协议应被过滤
  r = renderMarkdown('[evil](javascript:alert(1))')
  assertContains(r, 'href="#"', 'unsafe url blocked')
  console.log('✅ 链接')

  // 分隔线
  r = renderMarkdown('---')
  assertContains(r, '<hr class="md-hr"/>', 'hr')
  console.log('✅ 分隔线')

  // 表格
  r = renderMarkdown('| A | B |\n|---|---|\n| 1 | 2 |')
  assertContains(r, '<table class="md-table">', 'table')
  assertContains(r, '<th>A</th>', 'th A')
  assertContains(r, '<td>1</td>', 'td 1')
  console.log('✅ 表格')

  // XSS 防护
  r = renderMarkdown('<script>alert(1)</script>')
  assertContains(r, '&lt;script&gt;', 'XSS escaped')
  if (r.includes('<script>')) {
    console.error('❌ XSS blocked failed')
    process.exit(1)
  }
  console.log('✅ XSS 防护')

  // 空文本
  if (renderMarkdown('') !== '') {
    console.error('❌ empty input should produce empty output')
    process.exit(1)
  }
  console.log('✅ 空文本')

  // 段落合并
  r = renderMarkdown('第一段\n第二段')
  assertContains(r, '<p class="md-p">', 'p')
  assertContains(r, '<br/>', 'br')
  console.log('✅ 段落合并')

  console.log('\n🎉 全部 Markdown 测试通过！')
}

runTests()