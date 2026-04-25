"use client";

import type { QueueEntry } from "@/lib/actions/queue";
import { scoreColor, formatScore } from "@/lib/queue-utils";

export function QueueJobCard({
  item,
  onRemove,
  onAnalyze,
}: {
  item: QueueEntry;
  onRemove: (queueId: string) => void;
  onAnalyze: (queueId: string) => void;
}) {
  const { id, posting, company, overlapScore, matchedKeywords, missingKeywords, fitExplanation, analyzedAt } = item;

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-sm truncate">{posting.title || "Untitled"}</h3>
          <p className="text-xs text-muted">{company.name}</p>
        </div>
        <button
          onClick={() => onRemove(id)}
          className="text-xs text-muted hover:text-foreground transition-colors shrink-0"
          title="Remove from queue"
        >
          ✕
        </button>
      </div>

      {/* Score section */}
      {analyzedAt ? (
        <div className="space-y-2">
          <div className={`rounded p-2 ${scoreColor(overlapScore)}`}>
            <div className="flex items-baseline justify-between">
              <span className="text-xs font-medium">Fit Score</span>
              <span className="text-sm font-bold">{formatScore(overlapScore)}</span>
            </div>
          </div>

          {/* Keywords */}
          {matchedKeywords.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1">Matched ({matchedKeywords.length})</p>
              <div className="flex flex-wrap gap-1">
                {matchedKeywords.slice(0, 3).map((kw) => (
                  <span key={kw} className="inline-block rounded-full bg-green-100 px-2 py-1 text-xs font-medium">
                    {kw}
                  </span>
                ))}
                {matchedKeywords.length > 3 && (
                  <span className="inline-block rounded-full bg-border px-2 py-1 text-xs font-medium">
                    +{matchedKeywords.length - 3}
                  </span>
                )}
              </div>
            </div>
          )}

          {missingKeywords.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1">Missing ({missingKeywords.length})</p>
              <div className="flex flex-wrap gap-1">
                {missingKeywords.slice(0, 3).map((kw) => (
                  <span key={kw} className="inline-block rounded-full bg-border px-2 py-1 text-xs font-medium">
                    {kw}
                  </span>
                ))}
                {missingKeywords.length > 3 && (
                  <span className="inline-block rounded-full bg-border px-2 py-1 text-xs font-medium">
                    +{missingKeywords.length - 3}
                  </span>
                )}
              </div>
            </div>
          )}

          {fitExplanation && (
            <p className="text-xs text-muted">{fitExplanation}</p>
          )}
        </div>
      ) : (
        <button
          onClick={() => onAnalyze(id)}
          className="w-full py-2 px-3 text-xs font-medium rounded bg-primary text-primary-contrast hover:opacity-90 transition-opacity"
        >
          Analyze Fit
        </button>
      )}

      {/* Footer */}
      <div className="flex items-center gap-2 pt-2 border-t border-border text-xs text-muted">
        <a
          href={posting.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex-1 hover:text-foreground transition-colors truncate"
        >
          View posting
        </a>
      </div>
    </div>
  );
}
