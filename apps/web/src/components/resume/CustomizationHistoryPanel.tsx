"use client";

import { useEffect, useState } from "react";
import {
  getCustomizationHistory,
  getCustomizationCount,
  deleteCustomization,
  type CustomizationHistoryItem,
} from "@/lib/actions/customization-history";
import { Trash2 } from "lucide-react";

function formatRelativeTime(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - new Date(date).getTime();
  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffSecs / 60);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffSecs < 60) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 30) return `${diffDays}d ago`;
  return new Date(date).toLocaleDateString();
}

export function CustomizationHistoryPanel() {
  const [items, setItems] = useState<CustomizationHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const [deleting, setDeleting] = useState<string | null>(null);

  const limit = 10;

  useEffect(() => {
    async function loadHistory() {
      setLoading(true);
      try {
        const [historyItems, count] = await Promise.all([
          getCustomizationHistory({ limit, offset }),
          getCustomizationCount(),
        ]);
        setItems(historyItems);
        setTotal(count);
      } catch (err) {
        console.error("Failed to load customization history:", err);
        setItems([]);
        setTotal(0);
      } finally {
        setLoading(false);
      }
    }

    loadHistory();
  }, [offset]);

  const handleDelete = async (id: string) => {
    setDeleting(id);
    try {
      const result = await deleteCustomization(id);
      if (result.deleted) {
        setItems((prev) => prev.filter((item) => item.id !== id));
        setTotal((prev) => Math.max(0, prev - 1));
      }
    } catch (err) {
      console.error("Failed to delete customization:", err);
    } finally {
      setDeleting(null);
    }
  };

  const hasMore = offset + limit < total;
  const hasPrevious = offset > 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Customization History</h3>
        <span className="text-sm text-muted-foreground">{total} total</span>
      </div>

      {loading && items.length === 0 ? (
        <div className="flex justify-center py-8">
          <div className="animate-spin rounded-full h-6 w-6 border-2 border-primary border-transparent border-t-primary" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-muted/30 p-8 text-center">
          <p className="text-sm text-muted-foreground">No customizations yet</p>
        </div>
      ) : (
        <>
          <div className="space-y-2 max-h-96 overflow-y-auto rounded-lg border border-border bg-card">
            {items.map((item) => (
              <div
                key={item.id}
                className="flex items-start justify-between gap-3 border-b border-border p-3 last:border-b-0 hover:bg-muted/50 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="font-medium text-sm truncate">{item.jobTitle}</p>
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      {formatRelativeTime(item.createdAt)}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {item.insertedKeywords.slice(0, 3).map((keyword) => (
                      <span
                        key={keyword}
                        className="inline-block bg-green-100 text-green-800 text-xs px-2 py-0.5 rounded"
                      >
                        {keyword}
                      </span>
                    ))}
                    {item.insertedKeywords.length > 3 && (
                      <span className="text-xs text-muted-foreground">
                        +{item.insertedKeywords.length - 3} more
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(item.id)}
                  disabled={deleting === item.id}
                  className="mt-1 p-1.5 text-destructive hover:bg-destructive/10 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
                  aria-label={`Delete customization for ${item.jobTitle}`}
                >
                  {deleting === item.id ? (
                    <div className="animate-spin rounded-full h-4 w-4 border-2 border-destructive border-transparent border-t-destructive" />
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                </button>
              </div>
            ))}
          </div>

          {/* Pagination */}
          {(hasMore || hasPrevious) && (
            <div className="flex items-center justify-between gap-2 pt-2">
              <button
                onClick={() => setOffset(Math.max(0, offset - limit))}
                disabled={!hasPrevious || loading}
                className="px-3 py-1 text-sm border border-border rounded hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                Previous
              </button>
              <span className="text-xs text-muted-foreground">
                {offset + 1}-{Math.min(offset + limit, total)} of {total}
              </span>
              <button
                onClick={() => setOffset(offset + limit)}
                disabled={!hasMore || loading}
                className="px-3 py-1 text-sm border border-border rounded hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
