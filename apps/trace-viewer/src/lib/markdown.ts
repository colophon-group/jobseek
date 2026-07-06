/**
 * Lightweight markdown-to-HTML converter for trace viewer.
 * Handles: headers, bold, italic, code blocks, inline code, links, lists.
 * No external dependencies.
 */

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

  return out.join('\n')
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
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" class="md-link">$1</a>')
  return s
}
