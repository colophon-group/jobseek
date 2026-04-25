"use client";

import { useEffect, useState } from "react";
import { getQueueItems, removeFromQueue, analyzeQueueItem } from "@/lib/actions/queue";
import type { QueueEntry } from "@/lib/actions/queue";
import { QueueJobCard } from "@/components/queue/QueueJobCard";
import { Button } from "@/components/ui/Button";
import Link from "next/link";

const ITEMS_PER_PAGE = 20;

export default function QueuePage() {
  const [items, setItems] = useState<QueueEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadQueue() {
      try {
        const { items, total } = await getQueueItems({
          offset: 0,
          limit: ITEMS_PER_PAGE,
        });
        setItems(items);
        setTotal(total);
      } catch (err) {
        console.error("Failed to load queue:", err);
      } finally {
        setLoading(false);
      }
    }

    loadQueue();
  }, []);

  async function handleRemove(queueId: string) {
    try {
      await removeFromQueue(queueId);
      setItems((prev) => prev.filter((i) => i.id !== queueId));
      setTotal((prev) => Math.max(0, prev - 1));
    } catch (err) {
      console.error("Failed to remove from queue:", err);
    }
  }

  async function handleAnalyze(queueId: string) {
    try {
      const item = items.find((i) => i.id === queueId);
      if (!item) return;
      await analyzeQueueItem(queueId, item.posting.id);
      // Refetch the item to get updated analysis
      const { items: updated } = await getQueueItems({
        offset: 0,
        limit: ITEMS_PER_PAGE,
      });
      setItems(updated);
    } catch (err) {
      console.error("Failed to analyze queue item:", err);
    }
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto py-8 px-4">
        <div className="text-center text-muted">Loading queue...</div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto py-8 px-4 space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-bold">Job Queue</h1>
        <p className="text-muted">
          {total} {total === 1 ? "job" : "jobs"} in your queue
        </p>
      </div>

      {items.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center space-y-4">
          <p className="text-muted">Your queue is empty</p>
          <Link href="/explore">
            <Button variant="primary">Browse jobs</Button>
          </Link>
        </div>
      ) : (
        <div className="grid gap-4">
          {items.map((item) => (
            <QueueJobCard
              key={item.id}
              item={item}
              onRemove={handleRemove}
              onAnalyze={handleAnalyze}
            />
          ))}
        </div>
      )}
    </div>
  );
}
