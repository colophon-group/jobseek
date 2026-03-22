// ---- Raw trace record types ----

export interface TextBlock {
  type: 'text'
  text: string
}

export interface ThinkingBlock {
  type: 'thinking'
  thinking: string
  signature?: string
}

export interface ToolUseBlock {
  type: 'tool_use'
  id: string
  name: string
  input: Record<string, unknown>
}

export type ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock

export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens?: number
  cache_creation_input_tokens?: number
}

export interface BaseRecord {
  uuid: string
  parentUuid?: string
  sessionId?: string
  timestamp: string
  _scope?: string
  _agentType?: string
}

export interface UserRecord extends BaseRecord {
  type: 'user'
  message: {
    role: 'user'
    content: string | Array<{ type: string; tool_use_id?: string; content?: string | unknown[] }>
  }
  toolUseResult?: {
    stdout?: string
    stderr?: string
    exitCode?: number
    interrupted?: boolean
  }
  sourceToolAssistantUUID?: string
}

export interface AssistantRecord extends BaseRecord {
  type: 'assistant'
  message: {
    role: 'assistant'
    content: ContentBlock[]
    model?: string
    usage?: TokenUsage
  }
  costUSD?: number
}

export interface SystemRecord extends BaseRecord {
  type: 'system'
  message?: {
    role: 'system'
    content: string
  }
}

export interface ProgressRecord extends BaseRecord {
  type: 'progress'
}

export interface FileHistoryRecord extends BaseRecord {
  type: 'file-history-snapshot'
}

export interface QueueOperationRecord extends BaseRecord {
  type: 'queue-operation'
}

export type TraceRecord =
  | UserRecord
  | AssistantRecord
  | SystemRecord
  | ProgressRecord
  | FileHistoryRecord
  | QueueOperationRecord

// ---- Normalized timeline types ----

export type TimelineEventKind =
  | 'user-prompt'
  | 'assistant-text'
  | 'thinking'
  | 'tool-use'
  | 'tool-result'
  | 'system'

export interface TimelineEvent {
  id: number
  kind: TimelineEventKind
  timestamp: Date
  elapsedMs: number // from trace start
  // Content fields
  text: string
  fullText: string
  // Tool specific
  toolName?: string
  toolInput?: Record<string, unknown>
  toolId?: string
  // Tool result specific
  stdout?: string
  stderr?: string
  exitCode?: number
  // Token info (for assistant events)
  outputTokens?: number
  inputTokens?: number
  cacheReadTokens?: number
  model?: string
  // Subagent
  scope?: string
  agentType?: string
  isSubagent: boolean
  // Reference to raw record
  rawRecord: TraceRecord
}

export interface TraceStats {
  totalRecords: number
  totalTurns: number
  toolCalls: number
  totalInputTokens: number
  totalOutputTokens: number
  totalCacheReadTokens: number
  durationMs: number
  subagentCount: number
  modelBreakdown: Record<string, number>
  toolBreakdown: Record<string, number>
}

export type FilterMode = 'all' | 'tools' | 'text' | 'subagents' | 'thinking'

// ---- Trace bundle types (multi-trace JSONL) ----

export interface TraceHeader {
  _trace_header: true
  slug: string
  company_name: string
  board_slugs: string[]
  date: string
  issue: number | null
  record_count: number
}

export interface TraceBundle {
  header: TraceHeader
  records: TraceRecord[]
}
