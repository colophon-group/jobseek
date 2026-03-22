import React, { useRef, useEffect } from 'react'
import type { TimelineEvent } from '../types'
import TimelineRow from './TimelineRow'

interface TimelineProps {
  events: TimelineEvent[]
  selected: number | null
  onSelect: (id: number) => void
}

const TIME_GAP_THRESHOLD_MS = 5000

const Timeline: React.FC<TimelineProps> = ({ events, selected, onSelect }) => {
  const containerRef = useRef<HTMLDivElement>(null)

  // Scroll selected into view
  useEffect(() => {
    if (selected === null || !containerRef.current) return
    const el = containerRef.current.querySelector(`[data-event-id="${selected}"]`)
    if (el) {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [selected])

  const items: React.ReactNode[] = []

  for (let i = 0; i < events.length; i++) {
    const event = events[i]

    // Time gap divider
    if (i > 0) {
      const gap = event.elapsedMs - events[i - 1].elapsedMs
      if (gap > TIME_GAP_THRESHOLD_MS) {
        const gapSec = Math.round(gap / 1000)
        items.push(
          <div
            key={`gap-${i}`}
            className="flex items-center gap-2 px-4 py-0.5"
          >
            <div className="flex-1 h-px" style={{ background: 'var(--divider)' }} />
            <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
              {gapSec}s gap
            </span>
            <div className="flex-1 h-px" style={{ background: 'var(--divider)' }} />
          </div>
        )
      }
    }

    items.push(
      <div key={event.id} data-event-id={event.id}>
        <TimelineRow
          event={event}
          isSelected={selected === event.id}
          onClick={() => onSelect(event.id)}
        />
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="flex-1 overflow-y-auto"
      style={{ background: 'var(--background)' }}
    >
      {items}
    </div>
  )
}

export default React.memo(Timeline)
