"use client";

import { Bookmark, BookmarkCheck } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import { useSession } from "@/components/SessionProvider";
import { useLocalePath } from "@/lib/useLocalePath";
import { useSavedJobs } from "@/components/SavedJobsProvider";
import { tooltipClass } from "@/components/ui/tooltip-styles";

export function SaveButton({ postingId }: { postingId: string }) {
  const { t } = useLingui();
  const { isLoggedIn, isPending } = useSession();
  const lp = useLocalePath();
  const { isSaved, toggle, isToggling } = useSavedJobs();

  const saved = isSaved(postingId);
  const toggling = isToggling(postingId);

  const label = saved
    ? t({ id: "search.save.unsave", comment: "Tooltip for unsave job button", message: "Unsave job" })
    : t({ id: "search.save.save", comment: "Tooltip for save job button", message: "Save job" });

  function handleClick(e: React.MouseEvent) {
    e.stopPropagation();
    if (isPending) return;
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
            <Icon size={14} aria-hidden="true" className={saved ? "fill-current" : ""} />
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
