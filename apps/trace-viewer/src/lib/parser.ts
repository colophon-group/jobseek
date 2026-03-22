import type {
  TraceRecord,
  TraceHeader,
  TraceBundle,
  AssistantRecord,
  UserRecord,
  TimelineEvent,
  TimelineEventKind,
  TraceStats,
  ContentBlock,
} from '../types'

const SKIP_TYPES = new Set(['progress', 'file-history-snapshot', 'queue-operation'])

export function parseJsonl(text: string): TraceRecord[] {
  const records: TraceRecord[] = []
  const lines = text.split('\n')
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      const obj = JSON.parse(trimmed) as TraceRecord
      if (!SKIP_TYPES.has(obj.type)) {
        records.push(obj)
      }
    } catch {
      // Skip malformed lines
    }
  }
  return records
}

export function parseTraceBundle(text: string): TraceBundle[] {
  const bundles: TraceBundle[] = []
  let currentHeader: TraceHeader | null = null
  let currentRecords: TraceRecord[] = []

  const lines = text.split('\n')
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      const obj = JSON.parse(trimmed)
      if (obj._trace_header) {
        // Start a new bundle: flush any previous one
        if (currentHeader) {
          bundles.push({ header: currentHeader, records: currentRecords })
        }
        currentHeader = obj as TraceHeader
        currentRecords = []
      } else {
        const record = obj as TraceRecord
        if (!SKIP_TYPES.has(record.type)) {
          currentRecords.push(record)
        }
      }
    } catch {
      // Skip malformed lines
    }
  }
  // Flush the last bundle
  if (currentHeader) {
    bundles.push({ header: currentHeader, records: currentRecords })
  }

  // Sort by date, newest first
  bundles.sort((a, b) => b.header.date.localeCompare(a.header.date))

  return bundles
}

function truncate(s: string, maxLen: number): string {
  if (s.length <= maxLen) return s
  return s.slice(0, maxLen) + '...'
}

function extractToolDescription(name: string, input: Record<string, unknown>): string {
  switch (name) {
    case 'Bash':
      return truncate(String(input.command ?? ''), 80)
    case 'Read':
      return truncate(String(input.file_path ?? ''), 80)
    case 'Write':
      return truncate(String(input.file_path ?? ''), 80)
    case 'Edit':
      return truncate(String(input.file_path ?? ''), 80)
    case 'Grep':
      return truncate(`${input.pattern ?? ''} ${input.path ?? ''}`.trim(), 80)
    case 'Glob':
      return truncate(`${input.pattern ?? ''} ${input.path ?? ''}`.trim(), 80)
    case 'WebSearch':
      return truncate(String(input.query ?? ''), 80)
    case 'WebFetch':
      return truncate(String(input.url ?? ''), 80)
    case 'Agent':
    case 'Skill':
      return truncate(String(input.skill ?? input.type ?? ''), 80)
    case 'TodoWrite':
      return 'Update todo list'
    case 'ToolSearch':
      return truncate(String(input.query ?? ''), 80)
    default:
      return truncate(JSON.stringify(input).slice(0, 80), 80)
  }
}

function getUserText(record: UserRecord): string {
  const msg = record.message
  if (typeof msg.content === 'string') return msg.content
  if (Array.isArray(msg.content)) {
    const texts = msg.content
      .map((c) => {
        if (typeof c === 'string') return c
        if (c && typeof c === 'object' && 'content' in c) {
          if (typeof c.content === 'string') return c.content
        }
        return ''
      })
      .filter(Boolean)
    return texts.join('\n')
  }
  return ''
}

export function buildTimeline(records: TraceRecord[]): TimelineEvent[] {
  const events: TimelineEvent[] = []
  let idCounter = 0

  // Find first record with a valid timestamp for elapsed time calculation
  const firstTs = records.find((r) => r.timestamp)?.timestamp
  const startTime = firstTs ? new Date(firstTs).getTime() : 0

  // Build a map of tool_use IDs to their events for pairing with results
  const toolUseMap = new Map<string, TimelineEvent>()

  for (const record of records) {
    if (!record.timestamp) continue
    const ts = new Date(record.timestamp)
    const elapsed = ts.getTime() - startTime
    const elapsedMs = Number.isFinite(elapsed) ? elapsed : 0
    const isSubagent = record._scope?.startsWith('subagent:') ?? false
    const scope = record._scope
    const agentType = record._agentType

    if (record.type === 'assistant') {
      const assistantRec = record as AssistantRecord
      const content = assistantRec.message.content
      const usage = assistantRec.message.usage
      const model = assistantRec.message.model

      if (!Array.isArray(content)) continue

      let tokensClaimed = false // only show tokens on first event per message
      for (const block of content) {
        const cb = block as ContentBlock
        if (cb.type === 'text') {
          const text = cb.text.trim()
          if (!text) continue
          const showTokens = !tokensClaimed
          tokensClaimed = true
          events.push({
            id: idCounter++,
            kind: 'assistant-text',
            timestamp: ts,
            elapsedMs,
            text: truncate(text.split('\n')[0], 80),
            fullText: text,
            outputTokens: showTokens ? usage?.output_tokens : undefined,
            inputTokens: showTokens ? usage?.input_tokens : undefined,
            cacheReadTokens: showTokens ? usage?.cache_read_input_tokens : undefined,
            model,
            scope,
            agentType,
            isSubagent,
            rawRecord: record,
          })
        } else if (cb.type === 'thinking') {
          const thinking = cb.thinking?.trim() ?? ''
          events.push({
            id: idCounter++,
            kind: 'thinking',
            timestamp: ts,
            elapsedMs,
            text: thinking ? truncate(thinking.split('\n')[0], 60) : '[encrypted]',
            fullText: thinking || '[encrypted thinking block]',
            scope,
            agentType,
            isSubagent,
            rawRecord: record,
          })
        } else if (cb.type === 'tool_use') {
          const desc = extractToolDescription(cb.name, cb.input)
          const evt: TimelineEvent = {
            id: idCounter++,
            kind: 'tool-use',
            timestamp: ts,
            elapsedMs,
            text: `${cb.name}: ${desc}`,
            fullText: JSON.stringify(cb.input, null, 2),
            toolName: cb.name,
            toolInput: cb.input,
            toolId: cb.id,
            outputTokens: usage?.output_tokens,
            model,
            scope,
            agentType,
            isSubagent,
            rawRecord: record,
          }
          events.push(evt)
          toolUseMap.set(cb.id, evt)
        }
      }
    } else if (record.type === 'user') {
      const userRec = record as UserRecord
      if (userRec.toolUseResult) {
        // This is a tool result
        const result = userRec.toolUseResult
        const stdout = result.stdout ?? ''
        const stderr = result.stderr ?? ''
        const hasError = (result.exitCode !== undefined && result.exitCode !== 0) || !!stderr
        const preview = hasError
          ? truncate(stderr || stdout, 60)
          : stdout
            ? truncate(stdout.split('\n')[0], 60)
            : 'done'
        events.push({
          id: idCounter++,
          kind: 'tool-result',
          timestamp: ts,
          elapsedMs,
          text: hasError ? `error: ${preview}` : preview,
          fullText: stdout || stderr || '(no output)',
          stdout,
          stderr,
          exitCode: result.exitCode,
          scope,
          agentType,
          isSubagent,
          rawRecord: record,
        })
      } else {
        // Regular user prompt
        const text = getUserText(userRec)
        if (!text.trim()) continue
        events.push({
          id: idCounter++,
          kind: 'user-prompt',
          timestamp: ts,
          elapsedMs,
          text: truncate(text.split('\n')[0], 80),
          fullText: text,
          scope,
          agentType,
          isSubagent,
          rawRecord: record,
        })
      }
    } else if (record.type === 'system') {
      const sysContent =
        (record as any).message?.content ?? JSON.stringify(record)
      events.push({
        id: idCounter++,
        kind: 'system',
        timestamp: ts,
        elapsedMs,
        text: truncate(String(sysContent).split('\n')[0], 80),
        fullText: String(sysContent),
        scope,
        agentType,
        isSubagent,
        rawRecord: record,
      })
    }
  }

  return events
}

export function computeStats(records: TraceRecord[], events: TimelineEvent[]): TraceStats {
  let totalInputTokens = 0
  let totalOutputTokens = 0
  let totalCacheReadTokens = 0
  let totalTurns = 0
  let toolCalls = 0
  const subagentScopes = new Set<string>()
  const modelBreakdown: Record<string, number> = {}
  const toolBreakdown: Record<string, number> = {}

  for (const record of records) {
    if (record.type === 'assistant') {
      const ar = record as AssistantRecord
      totalTurns++
      const usage = ar.message.usage
      if (usage) {
        totalInputTokens += usage.input_tokens ?? 0
        totalOutputTokens += usage.output_tokens ?? 0
        totalCacheReadTokens += usage.cache_read_input_tokens ?? 0
      }
      const model = ar.message.model ?? 'unknown'
      modelBreakdown[model] = (modelBreakdown[model] ?? 0) + 1

      if (Array.isArray(ar.message.content)) {
        for (const block of ar.message.content) {
          if ((block as any).type === 'tool_use') {
            toolCalls++
            const name = (block as any).name ?? 'unknown'
            toolBreakdown[name] = (toolBreakdown[name] ?? 0) + 1
          }
        }
      }
    }
    if (record._scope?.startsWith('subagent:')) {
      subagentScopes.add(record._scope)
    }
  }

  const timestamps = records.map((r) => new Date(r.timestamp).getTime()).filter((t) => !isNaN(t))
  const durationMs =
    timestamps.length >= 2 ? Math.max(...timestamps) - Math.min(...timestamps) : 0

  return {
    totalRecords: records.length,
    totalTurns,
    toolCalls,
    totalInputTokens,
    totalOutputTokens,
    totalCacheReadTokens,
    durationMs,
    subagentCount: subagentScopes.size,
    modelBreakdown,
    toolBreakdown,
  }
}

export interface AgentInfo {
  scope: string        // "main" or "subagent:<id>"
  agentType: string    // "general-purpose", "Explore", "Plan", etc.
  label: string        // Display label: "Main Agent" or "Explore (abc...)" etc.
  eventCount: number
}

export function extractAgents(events: TimelineEvent[]): AgentInfo[] {
  // Group events by scope, preserving first-appearance order
  const scopeOrder: string[] = []
  const scopeMap = new Map<string, { agentType: string; count: number }>()

  for (const event of events) {
    const scope = event.scope ?? 'main'
    const existing = scopeMap.get(scope)
    if (existing) {
      existing.count++
    } else {
      scopeOrder.push(scope)
      scopeMap.set(scope, {
        agentType: event.agentType ?? 'general-purpose',
        count: 1,
      })
    }
  }

  // Build AgentInfo list: "main" always first, then subagents in first-appearance order
  const agents: AgentInfo[] = []

  // Always include main, even if no events (shouldn't happen, but be safe)
  const mainData = scopeMap.get('main')
  agents.push({
    scope: 'main',
    agentType: 'general-purpose',
    label: 'Main Agent',
    eventCount: mainData?.count ?? 0,
  })

  // Subagents in order of first appearance
  for (const scope of scopeOrder) {
    if (scope === 'main') continue
    const data = scopeMap.get(scope)!
    const agentId = scope.replace('subagent:', '')
    const truncatedId = agentId.slice(0, 6)
    const agentType = data.agentType
    const label = agentType !== 'general-purpose'
      ? `${agentType} (${truncatedId})`
      : `Subagent (${truncatedId})`
    agents.push({
      scope,
      agentType,
      label,
      eventCount: data.count,
    })
  }

  return agents
}

export function applyFilters(
  events: TimelineEvent[],
  filter: string,
  search: string
): TimelineEvent[] {
  let filtered = events

  switch (filter) {
    case 'tools':
      filtered = filtered.filter((e) => e.kind === 'tool-use' || e.kind === 'tool-result')
      break
    case 'text':
      filtered = filtered.filter(
        (e) => e.kind === 'assistant-text' || e.kind === 'user-prompt' || e.kind === 'system'
      )
      break
    case 'subagents':
      filtered = filtered.filter((e) => e.isSubagent)
      break
    case 'thinking':
      filtered = filtered.filter((e) => e.kind === 'thinking')
      break
  }

  if (search.trim()) {
    const q = search.toLowerCase()
    filtered = filtered.filter(
      (e) =>
        e.text.toLowerCase().includes(q) ||
        e.fullText.toLowerCase().includes(q) ||
        (e.toolName?.toLowerCase().includes(q) ?? false)
    )
  }

  return filtered
}
