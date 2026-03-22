import { useState, useMemo, useCallback, useEffect } from 'react'
import type { TimelineEvent, TraceStats, FilterMode, TraceBundle } from '../types'
import { parseJsonl, parseTraceBundle, buildTimeline, computeStats, applyFilters } from '../lib/parser'

export function useTrace() {
  const [allEvents, setAllEvents] = useState<TimelineEvent[]>([])
  const [stats, setStats] = useState<TraceStats | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [filter, setFilter] = useState<FilterMode>('all')
  const [search, setSearch] = useState('')
  const [filename, setFilename] = useState<string | null>(null)

  // Bundle state
  const [bundles, setBundles] = useState<TraceBundle[]>([])
  const [activeBundle, setActiveBundle] = useState<number | null>(null)
  const [serverLoaded, setServerLoaded] = useState(false)
  const [serverAttempted, setServerAttempted] = useState(false)

  const activateBundle = useCallback((index: number, bundleList?: TraceBundle[]) => {
    const list = bundleList ?? bundles
    if (index < 0 || index >= list.length) return
    const bundle = list[index]
    const timeline = buildTimeline(bundle.records)
    const traceStats = computeStats(bundle.records, timeline)
    setAllEvents(timeline)
    setStats(traceStats)
    setSelected(null)
    setFilter('all')
    setSearch('')
    setActiveBundle(index)
    setFilename(null)
  }, [bundles])

  const loadJsonl = useCallback((text: string, name?: string) => {
    // Try to parse as bundle format first
    const parsed = parseTraceBundle(text)
    if (parsed.length > 0) {
      setBundles(parsed)
      setServerLoaded(true)
      // Auto-select first bundle
      activateBundle(0, parsed)
      setFilename(name ?? null)
      return
    }

    // Fallback: single trace
    const records = parseJsonl(text)
    const timeline = buildTimeline(records)
    const traceStats = computeStats(records, timeline)
    setAllEvents(timeline)
    setStats(traceStats)
    setSelected(null)
    setFilter('all')
    setSearch('')
    setFilename(name ?? null)
  }, [activateBundle])

  const loadFromServer = useCallback(async () => {
    try {
      const resp = await fetch('/api/traces')
      if (!resp.ok) return
      const text = await resp.text()
      const parsed = parseTraceBundle(text)
      if (parsed.length > 0) {
        setBundles(parsed)
        setServerLoaded(true)
        // Auto-select first bundle
        activateBundle(0, parsed)
      }
    } catch {
      // Server not available, no-op
    } finally {
      setServerAttempted(true)
    }
  }, [activateBundle])

  // Try loading from server on mount
  useEffect(() => {
    loadFromServer()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(
    () => applyFilters(allEvents, filter, search),
    [allEvents, filter, search]
  )

  const selectedEvent = useMemo(
    () => (selected !== null ? allEvents.find((e) => e.id === selected) ?? null : null),
    [allEvents, selected]
  )

  const activeHeader = activeBundle !== null && bundles[activeBundle]
    ? bundles[activeBundle].header
    : null

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
    // Bundle API
    bundles,
    activeBundle,
    activeHeader,
    activateBundle,
    serverLoaded,
    serverAttempted,
  }
}
