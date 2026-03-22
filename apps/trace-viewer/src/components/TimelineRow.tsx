import React from 'react'
import {
  Terminal,
  FileText,
  Search,
  FolderSearch,
  Pencil,
  FileOutput,
  Globe,
  Bot,
  Wrench,
  MessageSquare,
  Brain,
  User,
  AlertCircle,
  CheckCircle,
  Cpu,
} from 'lucide-react'
import type { TimelineEvent } from '../types'

const TOOL_COLORS: Record<string, string> = {
  Bash: 'var(--success)',
  Read: 'var(--info)',
  Grep: '#eab308',
  Glob: '#06b6d4',
  Edit: '#f97316',
  Write: '#f97316',
  Agent: '#a855f7',
  Skill: '#a855f7',
  WebSearch: 'var(--error)',
  WebFetch: 'var(--error)',
  ToolSearch: '#06b6d4',
  TodoWrite: '#f97316',
  NotebookEdit: '#f97316',
}

const TOOL_ICONS: Record<string, React.ReactNode> = {
  Bash: <Terminal size={13} />,
  Read: <FileText size={13} />,
  Grep: <Search size={13} />,
  Glob: <FolderSearch size={13} />,
  Edit: <Pencil size={13} />,
  Write: <FileOutput size={13} />,
  Agent: <Bot size={13} />,
  Skill: <Bot size={13} />,
  WebSearch: <Globe size={13} />,
  WebFetch: <Globe size={13} />,
  ToolSearch: <Search size={13} />,
  TodoWrite: <Wrench size={13} />,
  NotebookEdit: <Pencil size={13} />,
}

const BORDER_COLORS: Record<string, string> = {
  'user-prompt': 'var(--info)',
  'assistant-text': 'transparent',
  thinking: 'transparent',
  'tool-use': 'transparent',
  'tool-result': 'transparent',
  system: 'var(--warning)',
}

const AGENT_TYPE_COLORS: Record<string, string> = {
  Explore: '#3b82f6',
  Plan: '#a855f7',
  default: '#71717a',
}

function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return `+${min}:${String(sec).padStart(2, '0')}`
}

interface TimelineRowProps {
  event: TimelineEvent
  isSelected: boolean
  onClick: () => void
}

const TimelineRow: React.FC<TimelineRowProps> = ({ event, isSelected, onClick }) => {
  const borderColor = BORDER_COLORS[event.kind] ?? 'transparent'

  const renderContent = () => {
    switch (event.kind) {
      case 'user-prompt':
        return (
          <div className="flex items-center gap-1.5">
            <User size={13} style={{ color: 'var(--info)', flexShrink: 0 }} />
            <span className="truncate">{event.text}</span>
          </div>
        )
      case 'assistant-text':
        return (
          <div className="flex items-center gap-1.5">
            <MessageSquare size={13} style={{ color: 'var(--foreground)', flexShrink: 0, opacity: 0.6 }} />
            <span className="truncate">{event.text}</span>
            {event.outputTokens && (
              <span
                className="text-[10px] px-1 py-0.5 rounded shrink-0"
                style={{ background: 'var(--surface-hover)', color: 'var(--muted)' }}
              >
                {event.outputTokens}t
              </span>
            )}
          </div>
        )
      case 'thinking':
        return (
          <div className="flex items-center gap-1.5 italic" style={{ color: 'var(--muted)' }}>
            <Brain size={13} style={{ flexShrink: 0 }} />
            <span className="truncate">{event.text}</span>
          </div>
        )
      case 'tool-use': {
        const color = TOOL_COLORS[event.toolName ?? ''] ?? 'var(--muted)'
        const icon = TOOL_ICONS[event.toolName ?? ''] ?? <Wrench size={13} />
        return (
          <div className="flex items-center gap-1.5">
            <span style={{ color, flexShrink: 0 }}>{icon}</span>
            <span className="truncate">{event.text}</span>
          </div>
        )
      }
      case 'tool-result': {
        const isError = event.exitCode !== undefined && event.exitCode !== 0
        return (
          <div
            className="flex items-center gap-1.5"
            style={{ color: isError ? 'var(--error)' : 'var(--muted)' }}
          >
            {isError ? (
              <AlertCircle size={13} style={{ flexShrink: 0 }} />
            ) : (
              <CheckCircle size={13} style={{ flexShrink: 0 }} />
            )}
            <span className="truncate">{event.text}</span>
          </div>
        )
      }
      case 'system':
        return (
          <div className="flex items-center gap-1.5" style={{ color: 'var(--warning)' }}>
            <Cpu size={13} style={{ flexShrink: 0 }} />
            <span className="truncate">{event.text}</span>
          </div>
        )
      default:
        return <span className="truncate">{event.text}</span>
    }
  }

  return (
    <div
      onClick={onClick}
      className="flex items-center gap-2 px-2 py-1.5 cursor-pointer text-xs"
      style={{
        borderLeft: `3px solid ${event.isSubagent ? (AGENT_TYPE_COLORS[event.agentType ?? ''] ?? AGENT_TYPE_COLORS.default) : borderColor}`,
        background: isSelected ? 'var(--surface-hover)' : 'transparent',
        paddingLeft: event.isSubagent ? 20 : 8,
      }}
      onMouseEnter={(e) => {
        if (!isSelected) (e.currentTarget as HTMLElement).style.background = 'var(--surface-hover)'
      }}
      onMouseLeave={(e) => {
        if (!isSelected) (e.currentTarget as HTMLElement).style.background = 'transparent'
      }}
    >
      {/* Elapsed time */}
      <span
        className="text-[10px] w-12 shrink-0 text-right tabular-nums"
        style={{ color: 'var(--muted)' }}
      >
        {formatElapsed(event.elapsedMs)}
      </span>

      {/* Subagent badge */}
      {event.isSubagent && event.agentType && (
        <span
          className="text-[9px] px-1 py-0.5 rounded shrink-0"
          style={{
            background: AGENT_TYPE_COLORS[event.agentType] ?? AGENT_TYPE_COLORS.default,
            color: '#fff',
          }}
        >
          {event.agentType}
        </span>
      )}

      {/* Content */}
      <div className="flex-1 min-w-0">{renderContent()}</div>
    </div>
  )
}

export default React.memo(TimelineRow)
