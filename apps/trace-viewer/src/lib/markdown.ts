/**
 * Lightweight markdown-to-HTML converter for trace viewer.
 * Handles: headers, bold, italic, code blocks, inline code, links, lists.
 * Trace text is untrusted: uploads and fetched traces both contain user/model
 * content. Escape it before formatting, validate every link destination, then
 * sanitize the generated HTML through a fixed DOMPurify allowlist.
 */

import DOMPurify, { type Config } from 'dompurify'
import { decodeHTML } from 'entities'

const URL_VALIDATION_BASE = new URL('https://trace-viewer.invalid/')
const DECODE_PASSES = 5
const RAW_WHITESPACE_OR_CONTROL = /[\u0000-\u0020\u007f]/
const UNSAFE_URL_CHARACTERS = /["'<>`]/

const MARKDOWN_SANITIZER_CONFIG: Config = {
  ALLOWED_TAGS: [
    'a',
    'br',
    'code',
    'em',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'li',
    'p',
    'pre',
    'strong',
    'ul',
  ],
  ALLOWED_ATTR: ['class', 'href', 'rel', 'target'],
  ALLOW_ARIA_ATTR: false,
  ALLOW_DATA_ATTR: false,
  FORBID_ATTR: ['style'],
  FORBID_TAGS: ['script', 'style'],
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function markdownToHtml(text: string): string {
  const lines = text.split('\n')
  const out: string[] = []
  let inCodeBlock = false
  let codeLines: string[] = []
  let inList = false

  for (const line of lines) {
    // Fenced code blocks
    if (line.trimStart().startsWith('```')) {
      if (inCodeBlock) {
        out.push(
          `<pre class="md-code-block"><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`
        )
        codeLines = []
        inCodeBlock = false
      } else {
        if (inList) {
          out.push('</ul>')
          inList = false
        }
        inCodeBlock = true
      }
      continue
    }

    if (inCodeBlock) {
      codeLines.push(line)
      continue
    }

    // Empty line
    if (!line.trim()) {
      if (inList) {
        out.push('</ul>')
        inList = false
      }
      out.push('<br/>')
      continue
    }

    // Headers
    const headerMatch = line.match(/^(#{1,6})\s+(.+)$/)
    if (headerMatch) {
      if (inList) {
        out.push('</ul>')
        inList = false
      }
      const level = headerMatch[1].length
      out.push(`<h${level} class="md-h${level}">${inlineFormat(headerMatch[2])}</h${level}>`)
      continue
    }

    // Unordered list
    if (line.match(/^\s*[-*]\s+/)) {
      if (!inList) {
        out.push('<ul class="md-list">')
        inList = true
      }
      const content = line.replace(/^\s*[-*]\s+/, '')
      out.push(`<li>${inlineFormat(content)}</li>`)
      continue
    }

    // Ordered list
    if (line.match(/^\s*\d+\.\s+/)) {
      if (!inList) {
        out.push('<ul class="md-list">')
        inList = true
      }
      const content = line.replace(/^\s*\d+\.\s+/, '')
      out.push(`<li>${inlineFormat(content)}</li>`)
      continue
    }

    // Normal paragraph line
    if (inList) {
      out.push('</ul>')
      inList = false
    }
    out.push(`<p class="md-p">${inlineFormat(line)}</p>`)
  }

  if (inCodeBlock) {
    out.push(`<pre class="md-code-block"><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
  }
  if (inList) {
    out.push('</ul>')
  }

  return DOMPurify.sanitize(out.join('\n'), MARKDOWN_SANITIZER_CONFIG)
}

function undoInputEscaping(value: string): string {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
}

function decodeHtmlEntities(value: string): string {
  let decoded = value
  for (let pass = 0; pass < DECODE_PASSES; pass++) {
    const next = decodeHTML(decoded)
    if (next === decoded) break
    decoded = next
  }
  return decoded
}

function decodePercentEncoding(value: string): string | null {
  let decoded = value
  for (let pass = 0; pass < DECODE_PASSES; pass++) {
    try {
      const next = decodeURIComponent(decoded)
      if (next === decoded) break
      decoded = next
    } catch {
      return null
    }
  }
  return decoded
}

type SafeMarkdownHref = {
  href: string
  external: boolean
}

function sanitizeMarkdownHref(escapedHref: string): SafeMarkdownHref | null {
  const rawHref = undoInputEscaping(escapedHref)
  if (!rawHref || RAW_WHITESPACE_OR_CONTROL.test(rawHref)) return null

  const entityDecoded = decodeHtmlEntities(rawHref)
  const canonical = decodePercentEncoding(entityDecoded)
  if (!canonical || UNSAFE_URL_CHARACTERS.test(canonical)) return null

  // Browsers ignore ASCII whitespace/control characters inside scheme names.
  // Collapse them for policy evaluation so `java\tscript:` is rejected even
  // when the original bytes reached us through an entity/percent encoding.
  const compact = canonical.replace(/[\u0000-\u0020\u007f]/g, '')
  const slashNormalized = compact.replace(/\\/g, '/')
  if (slashNormalized.startsWith('//')) return null

  let parsed: URL
  try {
    parsed = new URL(compact, URL_VALIDATION_BASE)
  } catch {
    return null
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null

  const explicitScheme = /^[a-z][a-z0-9+.-]*:/i.test(compact)
  return {
    href: escapeHtml(rawHref),
    external: explicitScheme || parsed.origin !== URL_VALIDATION_BASE.origin,
  }
}

function inlineFormat(text: string): string {
  let s = escapeHtml(text)
  // Inline code
  s = s.replace(/`([^`]+)`/g, '<code class="md-inline-code">$1</code>')
  // Bold + italic
  s = s.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
  // Bold
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  // Italic
  s = s.replace(/\*(.+?)\*/g, '<em>$1</em>')
  // Links
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_match, label: string, href: string) => {
    const safe = sanitizeMarkdownHref(href)
    if (!safe) return label
    const externalAttrs = safe.external
      ? ' target="_blank" rel="noopener noreferrer"'
      : ''
    return `<a href="${safe.href}" class="md-link"${externalAttrs}>${label}</a>`
  })
  return s
}
