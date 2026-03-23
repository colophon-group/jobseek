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
    const HF_REPO = 'viktoroo/jobseek-agent-traces'
    const HF_BASE = `https://huggingface.co/datasets/${HF_REPO}/resolve/main`

    try {
      // Single recursive API call to get all trace file paths
      const resp = await fetch(
        `https://huggingface.co/api/datasets/${HF_REPO}/tree/main/traces?recursive=true`
      )
      if (!resp.ok) return
      const tree: { type: string; path: string }[] = await resp.json()
      const traceFiles = tree
        .filter((f) => f.type === 'file' && f.path.endsWith('.jsonl'))
        .sort((a, b) => b.path.localeCompare(a.path)) // newest first

      if (traceFiles.length === 0) return

      // Build lightweight index entries (header-only bundles) for the sidebar,
      // then lazy-load full content when a bundle is activated.
      const stubs: TraceBundle[] = traceFiles.map((f) => {
        // Extract slug and date from path: traces/{slug}/{date}.jsonl
        const parts = f.path.replace('traces/', '').replace('.jsonl', '').split('/')
        const slug = parts[0] ?? ''
        const date = parts[1] ?? ''
        return {
          header: {
            _trace_header: true as const,
            slug,
            company_name: slug,
            board_slugs: [],
            date,
            issue: null,
            record_count: 0,
          },
          records: [],
          _hfPath: f.path, // stash for lazy loading
        } as TraceBundle & { _hfPath: string }
      })

      setBundles(stubs)
      setServerLoaded(true)
      setServerAttempted(true)

      // Eagerly load the first trace
      if (stubs.length > 0) {
        const first = stubs[0] as TraceBundle & { _hfPath: string }
        const traceResp = await fetch(`${HF_BASE}/${first._hfPath}`)
        if (traceResp.ok) {
          const text = await traceResp.text()
          const parsed = parseTraceBundle(text)
          if (parsed.length > 0) {
            stubs[0] = { ...parsed[0], _hfPath: first._hfPath } as TraceBundle & { _hfPath: string }
            setBundles([...stubs])
            activateBundle(0, stubs)
          }
        }
      }
    } catch {
      // HF not available, no-op
    } finally {
      setServerAttempted(true)
    }
  }, [activateBundle])

  // Lazy-load trace content when a bundle is activated
  const activateBundleWithFetch = useCallback(async (index: number, bundleList?: TraceBundle[]) => {
    const list = bundleList ?? bundles
    if (index < 0 || index >= list.length) return

    const bundle = list[index] as TraceBundle & { _hfPath?: string }

    // Already loaded — just activate
    if (bundle.records.length > 0) {
      activateBundle(index, list)
      return
    }

    // Need to fetch from HF
    if (!bundle._hfPath) {
      activateBundle(index, list)
      return
    }

    const HF_REPO = 'viktoroo/jobseek-agent-traces'
    const HF_BASE = `https://huggingface.co/datasets/${HF_REPO}/resolve/main`
    try {
      const resp = await fetch(`${HF_BASE}/${bundle._hfPath}`)
      if (!resp.ok) return
      const text = await resp.text()
      const parsed = parseTraceBundle(text)
      if (parsed.length > 0) {
        list[index] = { ...parsed[0], _hfPath: bundle._hfPath } as TraceBundle & { _hfPath: string }
        setBundles([...list])
        activateBundle(index, list)
      }
    } catch {
      // fetch failed, activate stub anyway
      activateBundle(index, list)
    }
  }, [bundles, activateBundle])

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
    activateBundle: activateBundleWithFetch,
    serverLoaded,
    serverAttempted,
  }
}
