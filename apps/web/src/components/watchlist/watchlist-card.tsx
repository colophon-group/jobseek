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
      className={`grid min-h-24 w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-4 rounded-lg border bg-surface px-4 py-3 text-left transition-colors ${
        active
          ? "border-primary ring-1 ring-primary/30"
          : "border-border-soft hover:border-primary/30 hover:bg-border-soft"
      }`}
    >
      <span className="min-w-0">
        <span className="line-clamp-2 text-sm font-semibold leading-snug">
          {watchlist.title}
        </span>
        {watchlist.description ? (
          <span className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted">
            {watchlist.description}
          </span>
        ) : null}
        <span
          className="mt-2 flex flex-wrap items-center gap-x-1.5 text-xs text-muted"
          aria-live="polite"
        >
          <span>
            {watchlist.anyCompany
              ? t({ id: "watchlists.card.allCompanies", comment: "Company scope shown for a watchlist that searches across every company", message: "All companies" })
              : <>{watchlist.companyCount} {watchlist.companyCount === 1
                  ? t({ id: "watchlists.card.companySingular", comment: "Singular company count on a watchlist card", message: "company" })
                  : t({ id: "watchlists.card.companyPlural", comment: "Plural company count on a watchlist card", message: "companies" })}</>}
          </span>
          {watchlist.activeJobCount == null ? null : (
            <>
              <span aria-hidden="true">&middot;</span>
              <span>
                {watchlist.activeJobCount} {watchlist.activeJobCount === 1
                  ? t({ id: "watchlists.card.jobSingular", comment: "Singular job count on watchlist card", message: "job" })
                  : t({ id: "watchlists.card.jobPlural", comment: "Plural job count on watchlist card", message: "jobs" })}
              </span>
            </>
          )}
        </span>
      </span>
      <span className="flex size-5 items-center justify-center text-primary" aria-hidden="true">
        {selecting
          ? <Loader2 size={16} className="motion-safe:animate-spin" />
          : active
            ? <Check size={16} />
            : null}
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
      className={`flex min-h-16 w-full items-center justify-center gap-2 rounded-lg border border-dashed border-border-soft bg-surface px-4 py-3 text-muted transition-colors ${
        creating || disabled
          ? "cursor-not-allowed opacity-50"
          : "cursor-pointer hover:border-primary/30 hover:text-foreground"
      }`}
    >
      {creating
        ? <Loader2 size={20} className="motion-safe:animate-spin" aria-hidden="true" />
        : <Plus size={20} aria-hidden="true" />}
      <span className="text-sm font-medium">
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
