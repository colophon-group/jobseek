"use client";

import { Inbox, InboxIcon } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import { useAuth } from "@/lib/useAuth";
import { useLocalePath } from "@/lib/useLocalePath";
import { useQueue } from "@/components/QueueProvider";
import { tooltipClass } from "@/components/ui/tooltip-styles";

export function QueueIconButton({ postingId }: { postingId: string }) {
  const { t } = useLingui();
  const { isLoggedIn, isPending } = useAuth();
  const lp = useLocalePath();
  const { isQueued, toggle, isToggling } = useQueue();

  const queued = isQueued(postingId);
  const toggling = isToggling(postingId);

  const label = queued
    ? t({ id: "queue.remove", comment: "Tooltip for remove from queue", message: "Remove from queue" })
    : t({ id: "queue.add", comment: "Tooltip for add to queue", message: "Add to queue" });

  function handleClick(e: React.MouseEvent) {
    e.stopPropagation();
    if (isPending) return;
    if (!isLoggedIn) {
      window.location.href = lp("/sign-in");
      return;
    }
    toggle(postingId);
  }

  return (
    <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button
            onClick={handleClick}
            disabled={toggling}
            className="shrink-0 cursor-pointer text-muted transition-opacity hover:opacity-70 disabled:cursor-default disabled:opacity-50"
            aria-label={label}
          >
            <Inbox size={14} className={queued ? "fill-current" : ""} />
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className={tooltipClass}
            sideOffset={6}
          >
            {label}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
