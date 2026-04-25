"use client";

import { useState } from "react";
import type { QueueEntry } from "@/lib/actions/queue";
import { customizeResume } from "@/lib/actions/customize-resume";
import { saveCustomization } from "@/lib/actions/save-customization";
import { scoreColor, formatScore } from "@/lib/queue-utils";
import { ResumeCustomizationModal } from "@/components/resume/ResumeCustomizationModal";

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
  const [modalOpen, setModalOpen] = useState(false);
  const [customizing, setCustomizing] = useState(false);
  const [customizationResult, setCustomizationResult] = useState<{
    original: string;
    customized_content: string;
    insertedKeywords: string[];
  } | null>(null);

  const handleCustomizeClick = async () => {
    setModalOpen(true);
    setCustomizing(true);
    try {
      const result = await customizeResume({
        jobTitle: posting.title || "Position",
        missingKeywords,
      });
      if (result.customized && result.customized_content) {
        setCustomizationResult({
          original: result.original,
          customized_content: result.customized_content,
          insertedKeywords: missingKeywords,
        });
      }
    } catch (err) {
      console.error("Failed to customize resume:", err);
    } finally {
      setCustomizing(false);
    }
  };

  const handleAcceptCustomization = async () => {
    if (!customizationResult) return;
    try {
      await saveCustomization({
        queueId: id,
        postingId: posting.id,
        customizedContent: customizationResult.customized_content,
        originalContent: customizationResult.original,
      });
      setModalOpen(false);
      setCustomizationResult(null);
    } catch (err) {
      console.error("Failed to save customization:", err);
    }
  };

  const handleCancel = () => {
    setModalOpen(false);
    setCustomizationResult(null);
  };

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

          {/* Customize Resume Button */}
          {missingKeywords.length > 0 && (
            <button
              onClick={handleCustomizeClick}
              disabled={customizing}
              className="w-full py-2 px-3 text-xs font-medium rounded bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {customizing ? "Customizing..." : "Customize Resume"}
            </button>
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

      {/* Customization Modal */}
      {customizationResult && (
        <ResumeCustomizationModal
          open={modalOpen}
          onOpenChange={setModalOpen}
          original={customizationResult.original}
          customized={customizationResult.customized_content}
          insertedKeywords={customizationResult.insertedKeywords}
          loading={false}
          onAccept={handleAcceptCustomization}
          onCancel={handleCancel}
        />
      )}
    </div>
  );
}
