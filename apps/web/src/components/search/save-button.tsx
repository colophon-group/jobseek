"use client";

import { Bookmark, BookmarkCheck } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import { useAuth } from "@/lib/useAuth";
import { useLocalePath } from "@/lib/useLocalePath";
import { useSavedJobs } from "@/components/SavedJobsProvider";

export function SaveButton({ postingId }: { postingId: string }) {
  const { t } = useLingui();
  const { isLoggedIn } = useAuth();
  const lp = useLocalePath();
  const { isSaved, toggle, isToggling } = useSavedJobs();

  const saved = isSaved(postingId);
  const toggling = isToggling(postingId);

  const label = saved
    ? t({ id: "search.save.unsave", comment: "Tooltip for unsave job button", message: "Unsave job" })
    : t({ id: "search.save.save", comment: "Tooltip for save job button", message: "Save job" });

  function handleClick(e: React.MouseEvent) {
    e.stopPropagation();
    if (!isLoggedIn) {
      window.location.href = lp("/sign-in");
      return;
    }
    toggle(postingId);
  }

  const Icon = saved ? BookmarkCheck : Bookmark;

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
            <Icon size={14} className={saved ? "fill-current" : ""} />
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="z-50 rounded-md bg-tooltip-bg px-2.5 py-1 text-xs text-white data-[state=delayed-open]:animate-[tooltip-in_150ms_ease] data-[state=instant-open]:animate-[tooltip-in_150ms_ease] data-[state=closed]:animate-[tooltip-out_100ms_ease_forwards]"
            sideOffset={6}
          >
            {label}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
