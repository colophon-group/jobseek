"use client";

import { AlertTriangle, Check, Loader2, Plus } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import type { UserWatchlistOverview } from "@/lib/actions/watchlists";
import { tooltipWarningClass } from "@/components/ui/tooltip-styles";

export function WatchlistCard({
  watchlist,
  active,
  selecting,
  onSelect,
}: {
  watchlist: UserWatchlistOverview;
  active: boolean;
  selecting: boolean;
  onSelect: () => void;
}) {
  const { t } = useLingui();

  return (
    <button
      type="button"
      aria-pressed={active}
      aria-busy={selecting}
      onClick={onSelect}
      className={`flex h-28 w-28 shrink-0 cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border bg-surface p-3 text-center transition-colors ${
        active
          ? "border-primary ring-1 ring-primary/30"
          : "border-border-soft hover:border-primary/30 hover:bg-border-soft"
      }`}
    >
      <span className="h-4 text-primary" aria-hidden="true">
        {selecting
          ? <Loader2 size={14} className="motion-safe:animate-spin" />
          : active
            ? <Check size={14} />
            : null}
      </span>
      <span className="line-clamp-2 text-xs font-medium leading-tight">
        {watchlist.title}
      </span>
      <span className="text-[10px] text-muted" aria-live="polite">
        {watchlist.activeJobCount == null ? (
          <>
            {watchlist.companyCount} {watchlist.companyCount === 1
              ? t({ id: "watchlists.card.companySingular", comment: "Singular company count shown while a watchlist job count loads", message: "company" })
              : t({ id: "watchlists.card.companyPlural", comment: "Plural company count shown while a watchlist job count loads", message: "companies" })}
          </>
        ) : (
          <>
            {watchlist.activeJobCount} {watchlist.activeJobCount === 1
              ? t({ id: "watchlists.card.jobSingular", comment: "Singular job count on watchlist card", message: "job" })
              : t({ id: "watchlists.card.jobPlural", comment: "Plural job count on watchlist card", message: "jobs" })}
          </>
        )}
      </span>
    </button>
  );
}

export function CreateWatchlistCard({
  onClick,
  creating,
  disabled,
}: {
  onClick: () => void;
  creating?: boolean;
  disabled?: boolean;
}) {
  const { t } = useLingui();

  const limitLabel = t({
    id: "watchlists.card.limitReached",
    comment: "Warning tooltip when the account-wide watchlist limit is reached",
    message: "Maximum of 10 watchlists reached",
  });

  const button = (
    <button
      type="button"
      onClick={() => {
        if (!creating && !disabled) onClick();
      }}
      aria-disabled={disabled || creating}
      aria-label={disabled ? limitLabel : undefined}
      className={`flex h-28 w-28 shrink-0 flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-border-soft bg-surface p-3 text-center text-muted transition-colors ${
        creating || disabled
          ? "cursor-not-allowed opacity-50"
          : "cursor-pointer hover:border-primary/30 hover:text-foreground"
      }`}
    >
      {creating
        ? <Loader2 size={20} className="motion-safe:animate-spin" aria-hidden="true" />
        : <Plus size={20} aria-hidden="true" />}
      <span className="text-xs font-medium">
        <Trans id="watchlists.card.create" comment="Label on the create watchlist card">
          Create
        </Trans>
      </span>
    </button>
  );

  if (!disabled) return button;

  return (
    <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>{button}</Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className={`${tooltipWarningClass} flex items-center gap-1.5`}
            sideOffset={6}
          >
            <AlertTriangle size={12} className="shrink-0" aria-hidden="true" />
            {limitLabel}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
