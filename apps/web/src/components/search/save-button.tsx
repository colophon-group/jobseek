"use client";

import { Bookmark, BookmarkCheck } from "lucide-react";
import { useAuth } from "@/lib/useAuth";
import { useLocalePath } from "@/lib/useLocalePath";
import { useSavedJobs } from "@/components/SavedJobsProvider";

export function SaveButton({ postingId }: { postingId: string }) {
  const { isLoggedIn } = useAuth();
  const lp = useLocalePath();
  const { isSaved, toggle, isToggling } = useSavedJobs();

  const saved = isSaved(postingId);
  const toggling = isToggling(postingId);

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
    <button
      onClick={handleClick}
      disabled={toggling}
      className="shrink-0 cursor-pointer text-muted transition-opacity hover:opacity-70 disabled:cursor-default disabled:opacity-50"
      aria-label={saved ? "Unsave job" : "Save job"}
    >
      <Icon size={14} className={saved ? "fill-current" : ""} />
    </button>
  );
}
