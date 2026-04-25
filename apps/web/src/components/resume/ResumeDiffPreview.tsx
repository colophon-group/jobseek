"use client";

import { useMemo } from "react";

export function ResumeDiffPreview({
  original,
  customized,
  insertedKeywords,
}: {
  original: string;
  customized: string;
  insertedKeywords: string[];
}) {
  const originalLines = useMemo(() => original.split("\n"), [original]);
  const customizedLines = useMemo(() => customized.split("\n"), [customized]);

  // Simple diff: highlight lines that changed
  const diff = useMemo(() => {
    const changes: Array<{ type: "unchanged" | "added" | "removed"; line: string; lineNum: number }> = [];

    // Find changed lines (simplified diff algorithm)
    const maxLines = Math.max(originalLines.length, customizedLines.length);

    for (let i = 0; i < maxLines; i++) {
      const origLine = originalLines[i] || "";
      const custLine = customizedLines[i] || "";

      if (origLine === custLine) {
        changes.push({ type: "unchanged", line: origLine, lineNum: i });
      } else if (origLine && !custLine) {
        changes.push({ type: "removed", line: origLine, lineNum: i });
      } else if (custLine && !origLine) {
        changes.push({ type: "added", line: custLine, lineNum: i });
      } else {
        changes.push({ type: "added", line: custLine, lineNum: i });
      }
    }

    return changes;
  }, [originalLines, customizedLines]);

  const highlightKeywords = (text: string): React.ReactNode[] => {
    let lastIndex = 0;
    const nodes: React.ReactNode[] = [];

    // Sort keywords by length (longest first) to avoid partial matches
    const sorted = [...insertedKeywords].sort((a, b) => b.length - a.length);

    for (const keyword of sorted) {
      const index = text.indexOf(keyword);
      if (index !== -1) {
        // Simple highlight: just mark positions
        const before = text.substring(0, index);
        const match = text.substring(index, index + keyword.length);
        const after = text.substring(index + keyword.length);

        return [
          before,
          <mark key={keyword} className="bg-green-200 font-semibold">
            {match}
          </mark>,
          after,
        ];
      }
    }

    return [text];
  };

  return (
    <div className="grid grid-cols-2 gap-4 rounded-lg border border-border bg-card p-4 font-mono text-xs">
      {/* Original */}
      <div>
        <h3 className="font-semibold mb-2 text-sm">Original</h3>
        <div className="max-h-96 overflow-y-auto bg-gray-50 p-2 rounded border border-border-soft">
          {originalLines.slice(0, 30).map((line, i) => (
            <div key={i} className="whitespace-pre-wrap break-words">
              {line}
            </div>
          ))}
          {originalLines.length > 30 && (
            <div className="text-muted italic">... {originalLines.length - 30} more lines</div>
          )}
        </div>
      </div>

      {/* Customized with highlighting */}
      <div>
        <h3 className="font-semibold mb-2 text-sm">Customized</h3>
        <div className="max-h-96 overflow-y-auto bg-green-50 p-2 rounded border border-green-200">
          {customizedLines.slice(0, 30).map((line, i) => (
            <div key={i} className="whitespace-pre-wrap break-words">
              {highlightKeywords(line)}
            </div>
          ))}
          {customizedLines.length > 30 && (
            <div className="text-muted italic">... {customizedLines.length - 30} more lines</div>
          )}
        </div>
      </div>

      {/* Summary */}
      <div className="col-span-2 space-y-2 pt-4 border-t border-border">
        <p className="text-sm font-medium">Changes Summary:</p>
        <div className="flex flex-wrap gap-2">
          {insertedKeywords.map((keyword) => (
            <span key={keyword} className="inline-block bg-green-100 px-2 py-1 rounded text-xs">
              <mark className="bg-green-200">{keyword}</mark>
            </span>
          ))}
        </div>
        <p className="text-xs text-muted">
          {insertedKeywords.length} keyword{insertedKeywords.length !== 1 ? "s" : ""} integrated into resume
        </p>
      </div>
    </div>
  );
}
