import { useState, useMemo, useCallback, useEffect } from 'react'
import type { TimelineEvent, TraceStats, FilterMode, TraceBundle } from '../types'
import { parseJsonl, parseTraceBundle, buildTimeline, computeStats, applyFilters, extractAgents } from '../lib/parser'
import type { AgentInfo } from '../lib/parser'

export function useTrace() {
  const [allEvents, setAllEvents] = useState<TimelineEvent[]>([])
  const [stats, setStats] = useState<TraceStats | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [filter, setFilter] = useState<FilterMode>('all')
  const [search, setSearch] = useState('')
  const [filename, setFilename] = useState<string | null>(null)

  // Agent tab state
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [activeAgent, setActiveAgent] = useState<string>('main')

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
    const agentList = extractAgents(timeline)
    setAgents(agentList)
    setActiveAgent('main')
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
    const agentList = extractAgents(timeline)
    setAgents(agentList)
    setActiveAgent('main')
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

  const filtered = useMemo(() => {
    // First filter by active agent scope
    const scopeFiltered = allEvents.filter((e) => {
      const eventScope = e.scope ?? 'main'
      return eventScope === activeAgent
    })
    // Then apply existing filter/search
    return applyFilters(scopeFiltered, filter, search)
  }, [allEvents, filter, search, activeAgent])

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
    // Agent tab API
    agents,
    activeAgent,
    setActiveAgent,
    // Bundle API
    bundles,
    activeBundle,
    activeHeader,
    activateBundle,
    serverLoaded,
    serverAttempted,
  }
}
