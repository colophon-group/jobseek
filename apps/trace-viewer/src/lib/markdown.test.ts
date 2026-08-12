/** @vitest-environment jsdom */

import { describe, expect, it } from 'vitest'
import { markdownToHtml } from './markdown'

function render(markdown: string): HTMLDivElement {
  const container = document.createElement('div')
  container.innerHTML = markdownToHtml(markdown)
  return container
}

describe('markdownToHtml link security', () => {
  it('keeps HTTPS links and isolates them from the viewer origin', () => {
    const container = render('[safe](https://example.com/jobs?a=1&b=2)')
    const link = container.querySelector('a')

    expect(link?.getAttribute('href')).toBe('https://example.com/jobs?a=1&b=2')
    expect(link?.getAttribute('target')).toBe('_blank')
    expect(link?.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it.each([
    '#event-1',
    '/traces/today',
    './next-trace',
    '../previous-trace',
    'trace/123?view=detail',
  ])('keeps safe relative destination %s in the same context', (href) => {
    const link = render(`[safe](${href})`).querySelector('a')

    expect(link?.getAttribute('href')).toBe(href)
    expect(link?.hasAttribute('target')).toBe(false)
    expect(link?.hasAttribute('rel')).toBe(false)
  })

  it.each([
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    'java\tscript:alert(1)',
    'java&#x0a;script:alert(1)',
    'java&Tab;script:alert(1)',
    'java&#x73;cript:alert(1)',
    '%6a%61%76%61%73%63%72%69%70%74%3aalert(1)',
    '%256a%2561%2576%2561%2573%2563%2572%2569%2570%2574%253aalert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    '//evil.example/payload',
    '\\\\evil.example/payload',
    '%2f%2fevil.example/payload',
    '&sol;&sol;evil.example/payload',
  ])('renders unsafe destination %s as inert text', (href) => {
    const container = render(`[click](${href})`)

    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).toContain('click')
  })

  it('cannot create tags or event handlers through raw HTML or a link attribute', () => {
    const container = render(
      '[click](https://example.com&quot; onclick=&quot;alert(1)) '
      + '<img src=x onerror=alert(1)><script>alert(1)</script>',
    )

    expect(container.querySelector('a')).toBeNull()
    expect(container.querySelector('img, script, [onclick], [onerror]')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>')
  })

  it('preserves normal markdown formatting through the final sanitizer', () => {
    const container = render('# Header\n\n**bold** and `code`\n\n- one\n- two')

    expect(container.querySelector('h1')?.textContent).toBe('Header')
    expect(container.querySelector('strong')?.textContent).toBe('bold')
    expect(container.querySelector('code')?.textContent).toBe('code')
    expect(container.querySelectorAll('li')).toHaveLength(2)
  })
})
