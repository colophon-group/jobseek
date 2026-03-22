import React from 'react'
import { markdownToHtml } from '../lib/markdown'
import type { TimelineEvent } from '../types'

interface DetailPanelProps {
  event: TimelineEvent | null
}

function JsonBlock({ data }: { data: unknown }) {
  return (
    <pre
      className="text-xs p-3 rounded overflow-x-auto"
      style={{
        background: 'var(--surface-hover)',
        color: 'var(--foreground)',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        fontFamily: 'var(--font-mono)',
      }}
    >
      {typeof data === 'string' ? data : JSON.stringify(data, null, 2)}
    </pre>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="text-[10px] font-bold uppercase tracking-wider mb-1"
      style={{ color: 'var(--muted)' }}
    >
      {children}
    </div>
  )
}

const DetailPanel: React.FC<DetailPanelProps> = ({ event }) => {
  if (!event) {
    return (
      <div
        className="flex items-center justify-center h-full text-sm"
        style={{ color: 'var(--muted)' }}
      >
        Select an event to view details
      </div>
    )
  }

  const renderUserPrompt = () => (
    <div className="space-y-3">
      <SectionLabel>User Prompt</SectionLabel>
      <div
        className="text-xs leading-relaxed"
        dangerouslySetInnerHTML={{ __html: markdownToHtml(event.fullText) }}
      />
    </div>
  )

  const renderAssistantText = () => (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <SectionLabel>Assistant</SectionLabel>
        {event.model && (
          <span
            className="text-[10px] px-1.5 py-0.5 rounded"
            style={{ background: 'var(--surface-hover)', color: 'var(--muted)' }}
          >
            {event.model}
          </span>
        )}
        {event.outputTokens != null && event.outputTokens > 10 && (
          <span
            className="text-[10px] px-1.5 py-0.5 rounded"
            style={{ background: 'var(--surface-hover)', color: 'var(--muted)' }}
          >
            {event.outputTokens} tokens
          </span>
        )}
      </div>
      <div
        className="text-xs leading-relaxed"
        dangerouslySetInnerHTML={{ __html: markdownToHtml(event.fullText) }}
      />
    </div>
  )

  const renderThinking = () => (
    <div className="space-y-3">
      <SectionLabel>Thinking</SectionLabel>
      <pre
        className="text-xs italic"
        style={{
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          fontFamily: 'var(--font-mono)',
          color: event.fullText.startsWith('[encrypted') ? 'var(--muted)' : 'var(--foreground)',
        }}
      >
        {event.fullText}
      </pre>
    </div>
  )

  const renderToolUse = () => {
    const isBash = event.toolName === 'Bash'
    return (
      <div className="space-y-4">
        {/* Tool header */}
        <div className="flex items-center gap-2">
          <SectionLabel>Tool Call</SectionLabel>
          <span
            className="text-[10px] px-1.5 py-0.5 rounded font-bold"
            style={{ background: 'var(--surface-hover)', color: 'var(--foreground)' }}
          >
            {event.toolName}
          </span>
          {event.toolId && (
            <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
              {event.toolId}
            </span>
          )}
        </div>

        {/* Input */}
        {isBash && event.toolInput?.command ? (
          <div className="space-y-2">
            <SectionLabel>Command</SectionLabel>
            <pre
              className="text-xs p-3 rounded overflow-x-auto"
              style={{
                background: 'var(--surface-hover)',
                color: 'var(--success)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                fontFamily: 'var(--font-mono)',
              }}
            >
              $ {String(event.toolInput.command)}
            </pre>
            {/* Other bash params */}
            {Object.keys(event.toolInput).filter(k => k !== 'command').length > 0 && (
              <>
                <SectionLabel>Parameters</SectionLabel>
                <JsonBlock
                  data={Object.fromEntries(
                    Object.entries(event.toolInput).filter(([k]) => k !== 'command')
                  )}
                />
              </>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            <SectionLabel>Input</SectionLabel>
            <JsonBlock data={event.toolInput} />
          </div>
        )}
      </div>
    )
  }

  const renderToolResult = () => {
    const isError = event.exitCode !== undefined && event.exitCode !== 0
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <SectionLabel>Tool Result</SectionLabel>
          {event.exitCode !== undefined && (
            <span
              className="text-[10px] px-1.5 py-0.5 rounded"
              style={{
                background: isError ? 'var(--error)' : 'var(--success)',
                color: '#fff',
              }}
            >
              exit {event.exitCode}
            </span>
          )}
        </div>

        {event.stdout && (
          <div className="space-y-1">
            <SectionLabel>stdout</SectionLabel>
            <pre
              className="text-xs p-3 rounded overflow-x-auto"
              style={{
                background: 'var(--surface-hover)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                fontFamily: 'var(--font-mono)',
                maxHeight: 600,
                overflowY: 'auto',
              }}
            >
              {event.stdout}
            </pre>
          </div>
        )}

        {event.stderr && (
          <div className="space-y-1">
            <SectionLabel>stderr</SectionLabel>
            <pre
              className="text-xs p-3 rounded overflow-x-auto"
              style={{
                background: 'var(--surface-hover)',
                color: 'var(--error)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                fontFamily: 'var(--font-mono)',
                maxHeight: 400,
                overflowY: 'auto',
              }}
            >
              {event.stderr}
            </pre>
          </div>
        )}

        {!event.stdout && !event.stderr && (
          <pre className="text-xs" style={{ color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
            {event.fullText}
          </pre>
        )}
      </div>
    )
  }

  const renderSystem = () => (
    <div className="space-y-3">
      <SectionLabel>System</SectionLabel>
      <pre
        className="text-xs"
        style={{
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          fontFamily: 'var(--font-mono)',
          color: 'var(--warning)',
        }}
      >
        {event.fullText}
      </pre>
    </div>
  )

  const renderByKind = () => {
    switch (event.kind) {
      case 'user-prompt':
        return renderUserPrompt()
      case 'assistant-text':
        return renderAssistantText()
      case 'thinking':
        return renderThinking()
      case 'tool-use':
        return renderToolUse()
      case 'tool-result':
        return renderToolResult()
      case 'system':
        return renderSystem()
      default:
        return <JsonBlock data={event.rawRecord} />
    }
  }

  return (
    <div className="p-4 overflow-y-auto h-full">
      {/* Metadata bar */}
      <div
        className="flex items-center gap-3 mb-4 pb-2 text-[10px]"
        style={{ borderBottom: '1px solid var(--divider)', color: 'var(--muted)' }}
      >
        <span>{event.timestamp instanceof Date && !isNaN(event.timestamp.getTime()) ? event.timestamp.toISOString() : ''}</span>
        {event.isSubagent && (
          <span
            className="px-1.5 py-0.5 rounded"
            style={{ background: 'var(--surface-hover)' }}
          >
            {event.scope}
          </span>
        )}
      </div>

      {renderByKind()}
    </div>
  )
}

export default React.memo(DetailPanel)
