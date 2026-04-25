"use client";

import { useQueue } from "@/components/QueueProvider";
import { Button } from "@/components/ui/Button";
import { Inbox } from "lucide-react";

export function QueueButton({
  postingId,
  className,
}: {
  postingId: string;
  className?: string;
}) {
  const { isQueued, toggle, isToggling } = useQueue();
  const queued = isQueued(postingId);
  const toggling = isToggling(postingId);

  return (
    <Button
      variant={queued ? "primary" : "outline"}
      size="sm"
      disabled={toggling}
      onClick={() => toggle(postingId)}
      className={`gap-2 ${className ?? ""}`}
      title={queued ? "Remove from queue" : "Add to queue"}
    >
      <Inbox className="h-4 w-4" />
      {queued ? "Queued" : "Queue"}
    </Button>
  );
}
