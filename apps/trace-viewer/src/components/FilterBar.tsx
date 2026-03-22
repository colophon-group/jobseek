import React from 'react'
import type { FilterMode } from '../types'

interface FilterBarProps {
  filter: FilterMode
  onFilterChange: (f: FilterMode) => void
  eventCount: number
  totalCount: number
}

const FILTERS: { value: FilterMode; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'tools', label: 'Tools' },
  { value: 'text', label: 'Text' },
  { value: 'thinking', label: 'Thinking' },
  { value: 'subagents', label: 'Subagents' },
]

const FilterBar: React.FC<FilterBarProps> = ({ filter, onFilterChange, eventCount, totalCount }) => {
  return (
    <div
      className="flex items-center gap-1 px-3 py-1.5 border-t"
      style={{
        borderColor: 'var(--divider)',
        background: 'var(--surface)',
      }}
    >
      {FILTERS.map((f) => (
        <button
          key={f.value}
          onClick={() => onFilterChange(f.value)}
          className="px-2 py-0.5 rounded text-[10px] cursor-pointer"
          style={{
            background: filter === f.value ? 'var(--foreground)' : 'transparent',
            color: filter === f.value ? 'var(--background)' : 'var(--muted)',
            border: `1px solid ${filter === f.value ? 'var(--foreground)' : 'var(--divider)'}`,
          }}
        >
          {f.label}
        </button>
      ))}
      <span className="text-[10px] ml-2" style={{ color: 'var(--muted)' }}>
        {eventCount === totalCount ? `${totalCount} events` : `${eventCount} / ${totalCount}`}
      </span>
    </div>
  )
}

export default React.memo(FilterBar)
