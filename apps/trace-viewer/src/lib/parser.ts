import type {
  TraceRecord,
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

  const startTime = records.length > 0 ? new Date(records[0].timestamp).getTime() : 0

  // Build a map of tool_use IDs to their events for pairing with results
  const toolUseMap = new Map<string, TimelineEvent>()

  for (const record of records) {
    const ts = new Date(record.timestamp)
    const elapsedMs = ts.getTime() - startTime
    const isSubagent = record._scope?.startsWith('subagent:') ?? false
    const scope = record._scope
    const agentType = record._agentType

    if (record.type === 'assistant') {
      const assistantRec = record as AssistantRecord
      const content = assistantRec.message.content
      const usage = assistantRec.message.usage
      const model = assistantRec.message.model

      if (!Array.isArray(content)) continue

      for (const block of content) {
        const cb = block as ContentBlock
        if (cb.type === 'text') {
          const text = cb.text.trim()
          if (!text) continue
          events.push({
            id: idCounter++,
            kind: 'assistant-text',
            timestamp: ts,
            elapsedMs,
            text: truncate(text.split('\n')[0], 80),
            fullText: text,
            outputTokens: usage?.output_tokens,
            inputTokens: usage?.input_tokens,
            cacheReadTokens: usage?.cache_read_input_tokens,
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
