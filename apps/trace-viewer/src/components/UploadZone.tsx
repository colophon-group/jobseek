import React, { useCallback, useRef, useState } from 'react'
import { Upload } from 'lucide-react'

interface UploadZoneProps {
  onLoad: (text: string, name: string) => void
}

const UploadZone: React.FC<UploadZoneProps> = ({ onLoad }) => {
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

  return (
    <div
      className="flex items-center justify-center h-full"
      style={{ background: 'var(--background)' }}
    >
      <div
        className="flex flex-col items-center gap-4 p-12 rounded-lg cursor-pointer"
        style={{
          border: `2px dashed ${dragging ? 'var(--info)' : 'var(--divider)'}`,
          background: dragging ? 'var(--surface-hover)' : 'var(--surface)',
          transition: 'border-color 0.15s, background 0.15s',
        }}
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        <Upload size={32} style={{ color: 'var(--muted)' }} />
        <div className="text-sm" style={{ color: 'var(--foreground)' }}>
          Drop a trace JSONL file here
        </div>
        <div className="text-xs" style={{ color: 'var(--muted)' }}>
          or click to browse
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".jsonl,.json"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) handleFile(file)
          }}
          className="hidden"
        />
      </div>
    </div>
  )
}

export default React.memo(UploadZone)
