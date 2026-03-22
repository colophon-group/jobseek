import React, { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { TimelineEvent } from '../types'

interface TokenChartProps {
  events: TimelineEvent[]
  onClickTurn: (eventId: number) => void
}

const TokenChart: React.FC<TokenChartProps> = ({ events, onClickTurn }) => {
  const [expanded, setExpanded] = useState(false)
  const [hovered, setHovered] = useState<number | null>(null)

  // Collect assistant text events that have token info (one per turn)
  const turns = events.filter(
    (e) => e.kind === 'assistant-text' && e.outputTokens && e.outputTokens > 0
  )

  if (turns.length === 0) return null

  const maxTokens = Math.max(...turns.map((t) => t.outputTokens ?? 0))

  return (
    <div
      className="border-b"
      style={{ borderColor: 'var(--divider)', background: 'var(--surface)' }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 px-3 py-1 text-[10px] cursor-pointer w-full"
        style={{ color: 'var(--muted)' }}
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Token chart ({turns.length} turns)
      </button>

      {expanded && (
        <div className="px-3 pb-2">
          <div className="flex items-end gap-px" style={{ height: 48 }}>
            {turns.map((turn, i) => {
              const tokens = turn.outputTokens ?? 0
              const heightPct = maxTokens > 0 ? (tokens / maxTokens) * 100 : 0
              const isHovered = hovered === i
              return (
                <div
                  key={turn.id}
                  className="relative cursor-pointer"
                  style={{
                    flex: 1,
                    maxWidth: 12,
                    minWidth: 2,
                    height: `${Math.max(heightPct, 4)}%`,
                    background: isHovered ? 'var(--info)' : 'var(--muted)',
                    borderRadius: '2px 2px 0 0',
                    opacity: isHovered ? 1 : 0.5,
                    transition: 'opacity 0.1s',
                  }}
                  onClick={() => onClickTurn(turn.id)}
                  onMouseEnter={() => setHovered(i)}
                  onMouseLeave={() => setHovered(null)}
                >
                  {isHovered && (
                    <div
                      className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-1.5 py-0.5 rounded text-[9px] whitespace-nowrap z-10"
                      style={{
                        background: 'var(--foreground)',
                        color: 'var(--background)',
                      }}
                    >
                      Turn {i + 1}: {tokens}t
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export default React.memo(TokenChart)
