import React, { useCallback, useRef, useState } from 'react'
import { Upload, Moon, Sun, Search, X } from 'lucide-react'
import type { TraceStats } from '../types'

function formatDuration(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  if (min === 0) return `${sec}s`
  return `${min}m ${sec}s`
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

interface TopBarProps {
  stats: TraceStats | null
  filename: string | null
  search: string
  onSearchChange: (q: string) => void
  onLoad: (text: string, name: string) => void
  darkMode: boolean
  onToggleDark: () => void
}

const TopBar: React.FC<TopBarProps> = ({
  stats,
  filename,
  search,
  onSearchChange,
  onLoad,
  darkMode,
  onToggleDark,
}) => {
  const fileRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handleFile = useCallback(
    (file: File) => {
      const reader = new FileReader()
      reader.onload = () => {
        if (typeof reader.result === 'string') {
          onLoad(reader.result, file.name)
        }
      }
      reader.readAsText(file)
    },
    [onLoad]
  )

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      const file = e.dataTransfer.files[0]
      if (file) handleFile(file)
    },
    [handleFile]
  )

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) handleFile(file)
    },
    [handleFile]
  )

  return (
    <div
      className="flex items-center gap-3 px-4 py-2 border-b"
      style={{
        borderColor: 'var(--divider)',
        background: 'var(--surface)',
        minHeight: 48,
      }}
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
      {/* Upload button */}
      <button
        onClick={() => fileRef.current?.click()}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium cursor-pointer shrink-0"
        style={{
          background: dragging ? 'var(--info)' : 'var(--surface-hover)',
          color: dragging ? '#fff' : 'var(--foreground)',
          border: `1px solid ${dragging ? 'var(--info)' : 'var(--divider)'}`,
        }}
      >
        <Upload size={14} />
        {filename ? 'Replace' : 'Open JSONL'}
      </button>
      <input
        ref={fileRef}
        type="file"
        accept=".jsonl,.json"
        onChange={handleInputChange}
        className="hidden"
      />

      {/* Filename */}
      {filename && (
        <span className="text-xs truncate max-w-48" style={{ color: 'var(--muted)' }}>
          {filename}
        </span>
      )}

      {/* Stats */}
      {stats && (
        <div
          className="flex items-center gap-3 text-xs shrink-0"
          style={{ color: 'var(--muted)' }}
        >
          <span>{stats.totalTurns} turns</span>
          <span>{stats.toolCalls} tools</span>
          <span>{formatTokens(stats.totalOutputTokens)} out</span>
          <span>{formatTokens(stats.totalInputTokens)} in</span>
          {stats.totalCacheReadTokens > 0 && (
            <span>{formatTokens(stats.totalCacheReadTokens)} cached</span>
          )}
          <span>{formatDuration(stats.durationMs)}</span>
          {stats.subagentCount > 0 && <span>{stats.subagentCount} subagents</span>}
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Search */}
      {stats && (
        <div
          className="flex items-center gap-1.5 px-2 py-1 rounded text-xs"
          style={{
            background: 'var(--surface-hover)',
            border: '1px solid var(--divider)',
          }}
        >
          <Search size={13} style={{ color: 'var(--muted)' }} />
          <input
            type="text"
            placeholder="Search..."
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            className="bg-transparent border-none outline-none text-xs w-40"
            style={{ color: 'var(--foreground)', fontFamily: 'var(--font-mono)' }}
          />
          {search && (
            <button
              onClick={() => onSearchChange('')}
              className="cursor-pointer"
              style={{ color: 'var(--muted)' }}
            >
              <X size={12} />
            </button>
          )}
        </div>
      )}

      {/* Dark mode toggle */}
      <button
        onClick={onToggleDark}
        className="p-1.5 rounded cursor-pointer"
        style={{ color: 'var(--muted)' }}
      >
        {darkMode ? <Sun size={16} /> : <Moon size={16} />}
      </button>
    </div>
  )
}

export default React.memo(TopBar)
