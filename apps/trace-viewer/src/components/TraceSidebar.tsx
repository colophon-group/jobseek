import React, { useState, useCallback, useRef } from 'react'
import { ChevronRight, ChevronDown, Upload } from 'lucide-react'
import type { TraceBundle } from '../types'

interface TraceSidebarProps {
  bundles: TraceBundle[]
  activeBundle: number | null
  onSelectBundle: (index: number) => void
  onUpload: (text: string, name: string) => void
}

interface CompanyGroup {
  slug: string
  companyName: string
  entries: { bundleIndex: number; bundle: TraceBundle }[]
}

const TraceSidebar: React.FC<TraceSidebarProps> = ({
  bundles,
  activeBundle,
  onSelectBundle,
  onUpload,
}) => {
  const [expandedSlugs, setExpandedSlugs] = useState<Set<string>>(() => {
    // Auto-expand the company of the active bundle
    if (activeBundle !== null && bundles[activeBundle]) {
      return new Set([bundles[activeBundle].header.slug])
    }
    return new Set()
  })

  const fileRef = useRef<HTMLInputElement>(null)

  const toggleSlug = useCallback((slug: string) => {
    setExpandedSlugs((prev) => {
      const next = new Set(prev)
      if (next.has(slug)) {
        next.delete(slug)
      } else {
        next.add(slug)
      }
      return next
    })
  }, [])

  // Group bundles by company slug, alphabetical
  const groups: CompanyGroup[] = React.useMemo(() => {
    const map = new Map<string, CompanyGroup>()
    bundles.forEach((bundle, index) => {
      const slug = bundle.header.slug
      if (!map.has(slug)) {
        map.set(slug, {
          slug,
          companyName: bundle.header.company_name,
          entries: [],
        })
      }
      map.get(slug)!.entries.push({ bundleIndex: index, bundle })
    })
    const sorted = Array.from(map.values()).sort((a, b) =>
      a.slug.localeCompare(b.slug)
    )
    return sorted
  }, [bundles])

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (!file) return
      const reader = new FileReader()
      reader.onload = () => {
        if (typeof reader.result === 'string') {
          onUpload(reader.result, file.name)
        }
      }
      reader.readAsText(file)
    },
    [onUpload]
  )

  return (
    <div
      className="flex flex-col h-full"
      style={{
        width: 220,
        minWidth: 220,
        background: 'var(--surface)',
        borderLeft: '1px solid var(--divider)',
      }}
    >
      {/* Header */}
      <div
        className="px-3 py-2 text-[10px] font-bold uppercase tracking-wider border-b"
        style={{ color: 'var(--muted)', borderColor: 'var(--divider)' }}
      >
        Traces
      </div>

      {/* Scrollable list */}
      <div className="flex-1 overflow-y-auto">
        {groups.map((group) => {
          const isExpanded = expandedSlugs.has(group.slug)
          return (
            <div key={group.slug}>
              {/* Company header */}
              <button
                onClick={() => toggleSlug(group.slug)}
                className="flex items-center gap-1 w-full px-2 py-1.5 text-left cursor-pointer"
                style={{ color: 'var(--foreground)' }}
              >
                {isExpanded ? (
                  <ChevronDown size={12} style={{ color: 'var(--muted)', flexShrink: 0 }} />
                ) : (
                  <ChevronRight size={12} style={{ color: 'var(--muted)', flexShrink: 0 }} />
                )}
                <span className="text-xs font-bold truncate">{group.companyName}</span>
                <span
                  className="text-[10px] ml-auto shrink-0"
                  style={{ color: 'var(--muted)' }}
                >
                  {group.entries.length}
                </span>
              </button>

              {/* Trace entries */}
              {isExpanded &&
                group.entries.map(({ bundleIndex, bundle }) => {
                  const isActive = activeBundle === bundleIndex
                  const header = bundle.header
                  return (
                    <button
                      key={bundleIndex}
                      onClick={() => onSelectBundle(bundleIndex)}
                      className="flex flex-col w-full pl-6 pr-2 py-1 text-left cursor-pointer"
                      style={{
                        background: isActive ? 'var(--info)' : 'transparent',
                        color: isActive ? '#fff' : 'var(--foreground)',
                      }}
                      title={`Boards: ${header.board_slugs.join(', ')}`}
                    >
                      <span
                        className="text-[11px]"
                        style={{ color: isActive ? '#fff' : 'var(--foreground)' }}
                      >
                        {header.date}
                      </span>
                      <span
                        className="text-[10px]"
                        style={{ color: isActive ? 'rgba(255,255,255,0.7)' : 'var(--muted)' }}
                      >
                        {header.record_count} records
                        {header.issue ? ` #${header.issue}` : ''}
                      </span>
                    </button>
                  )
                })}
            </div>
          )
        })}
      </div>

      {/* Upload button at bottom */}
      <div
        className="px-3 py-2 border-t"
        style={{ borderColor: 'var(--divider)' }}
      >
        <button
          onClick={() => fileRef.current?.click()}
          className="flex items-center justify-center gap-1.5 w-full px-2 py-1.5 rounded text-xs cursor-pointer"
          style={{
            background: 'var(--surface-hover)',
            color: 'var(--muted)',
            border: '1px solid var(--divider)',
          }}
        >
          <Upload size={12} />
          Upload JSONL
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".jsonl,.json"
          onChange={handleFileChange}
          className="hidden"
        />
      </div>
    </div>
  )
}

export default React.memo(TraceSidebar)
