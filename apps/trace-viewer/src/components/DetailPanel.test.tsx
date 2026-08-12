/** @vitest-environment jsdom */

import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import type { TimelineEvent, TimelineEventKind } from '../types'
import DetailPanel from './DetailPanel'

function maliciousEvent(kind: TimelineEventKind): TimelineEvent {
  return {
    id: 1,
    kind,
    timestamp: new Date('2026-08-11T00:00:00Z'),
    elapsedMs: 0,
    text: 'click',
    fullText:
      '[click](java&#x0a;script:alert&#40;document.domain&#41;) '
      + '<img src=x onerror=alert(1)><script>alert(1)</script>',
    isSubagent: false,
    rawRecord: {
      type: 'system',
      uuid: 'malicious-record',
      timestamp: '2026-08-11T00:00:00Z',
    },
  }
}

describe('DetailPanel trace content security', () => {
  it.each(['user-prompt', 'assistant-text'] as const)(
    'renders a crafted %s trace without executable content',
    (kind) => {
      const container = document.createElement('div')
      const root = createRoot(container)

      flushSync(() => root.render(<DetailPanel event={maliciousEvent(kind)} />))

      expect(container.querySelector('a, img, script, [onclick], [onerror]')).toBeNull()
      expect(container.textContent).toContain('click')
      expect(container.textContent).toContain('<img src=x onerror=alert(1)>')

      root.unmount()
    },
  )
})
