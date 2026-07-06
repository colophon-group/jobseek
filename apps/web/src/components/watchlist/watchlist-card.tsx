"use client";

import Link from "next/link";
import { Plus, Eye, Lock, Loader2, AlertTriangle } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import { useLocalePath } from "@/lib/useLocalePath";
import { scrollToTopOnNav } from "@/lib/scroll-on-nav";
import type { WatchlistSummary } from "@/lib/actions/watchlists";
import { UpgradeModal, useUpgradeModal } from "@/components/ui/upgrade-modal";
import { tooltipWarningClass } from "@/components/ui/tooltip-styles";

export function WatchlistCard({ watchlist, ownerUsername }: { watchlist: WatchlistSummary; ownerUsername: string | null }) {
  const { t } = useLingui();
  const lp = useLocalePath();
  const href = ownerUsername ? lp(`/${ownerUsername}/${watchlist.slug}`) : "#";

  return (
    <Link
      href={href}
      prefetch={false}
      onClick={() => scrollToTopOnNav(href)}
      className="flex h-28 w-28 shrink-0 flex-col items-center justify-center gap-1.5 rounded-lg border border-border-soft bg-surface p-3 text-center transition-colors hover:border-primary/30 hover:bg-border-soft"
    >
      <div className="flex items-center gap-1 text-muted">
        {watchlist.isPublic ? <Eye size={14} /> : <Lock size={14} />}
      </div>
      <span className="line-clamp-2 text-xs font-medium leading-tight">
        {watchlist.title}
      </span>
      <span className="text-[10px] text-muted">
        {watchlist.activeJobCount} {watchlist.activeJobCount === 1
          ? t({ id: "watchlists.card.jobSingular", comment: "Singular job count on watchlist card", message: "job" })
          : t({ id: "watchlists.card.jobPlural", comment: "Plural job count on watchlist card", message: "jobs" })}
      </span>
    </Link>
  );
}

export function CreateWatchlistCard({ onClick, creating, disabled }: { onClick: () => void; creating?: boolean; disabled?: boolean }) {
  const { t } = useLingui();
  const upgrade = useUpgradeModal();

  function handleClick() {
    if (creating) return;
    if (disabled) {
      upgrade.show(t({
        id: "upgrade.reason.watchlistLimit",
        comment: "Reason shown in upgrade modal when watchlist creation limit reached",
        message: "You've reached your watchlist limit. Upgrade your plan to create more watchlists.",
      }));
      return;
    }
    onClick();
  }

  const warningLabel = t({
    id: "watchlists.card.limitReached",
    comment: "Warning tooltip when watchlist limit reached on create card",
    message: "Watchlist limit reached",
  });

  const button = (
    <button
      type="button"
      onClick={handleClick}
      className={`flex h-28 w-28 shrink-0 flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-border-soft bg-surface p-3 text-center text-muted transition-colors cursor-pointer ${
        creating || disabled
          ? "opacity-50"
          : "hover:border-primary/30 hover:text-foreground"
      }`}
    >
      {creating ? <Loader2 size={20} className="animate-spin" /> : <Plus size={20} />}
      <span className="text-xs font-medium">
        <Trans id="watchlists.card.create" comment="Label on the create watchlist card">
          Create
        </Trans>
      </span>
    </button>
  );

  return (
    <>
      {disabled ? (
        <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
          <Tooltip.Root>
            <Tooltip.Trigger asChild>
              {button}
            </Tooltip.Trigger>
            <Tooltip.Portal>
              <Tooltip.Content className={`${tooltipWarningClass} flex items-center gap-1.5`} sideOffset={6}>
                <AlertTriangle size={12} className="shrink-0" />
                {warningLabel}
              </Tooltip.Content>
            </Tooltip.Portal>
          </Tooltip.Root>
        </Tooltip.Provider>
      ) : (
        button
      )}
      <UpgradeModal open={upgrade.open} onOpenChange={upgrade.setOpen} reason={upgrade.reason} />
    </>
  );
}
