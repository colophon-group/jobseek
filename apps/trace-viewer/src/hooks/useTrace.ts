import { useState, useMemo, useCallback } from 'react'
import type { TimelineEvent, TraceStats, FilterMode } from '../types'
import { parseJsonl, buildTimeline, computeStats, applyFilters } from '../lib/parser'

export function useTrace() {
  const [allEvents, setAllEvents] = useState<TimelineEvent[]>([])
  const [stats, setStats] = useState<TraceStats | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [filter, setFilter] = useState<FilterMode>('all')
  const [search, setSearch] = useState('')
  const [filename, setFilename] = useState<string | null>(null)

  const loadJsonl = useCallback((text: string, name?: string) => {
    const records = parseJsonl(text)
    const timeline = buildTimeline(records)
    const traceStats = computeStats(records, timeline)
    setAllEvents(timeline)
    setStats(traceStats)
    setSelected(null)
    setFilter('all')
    setSearch('')
    setFilename(name ?? null)
  }, [])

  const filtered = useMemo(
    () => applyFilters(allEvents, filter, search),
    [allEvents, filter, search]
  )

  const selectedEvent = useMemo(
    () => (selected !== null ? allEvents.find((e) => e.id === selected) ?? null : null),
    [allEvents, selected]
  )

  return {
    events: filtered,
    allEvents,
    stats,
    selected,
    selectedEvent,
    setSelected,
    filter,
    setFilter,
    search,
    setSearch,
    loadJsonl,
    filename,
  }
}
