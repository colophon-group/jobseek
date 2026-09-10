"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import {
  AlertTriangle,
  Building2,
  Check,
  ChevronRight,
  Loader2,
  Plus,
  Share2,
  Trash2,
} from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import type {
  UserWatchlistActivityPreview,
  UserWatchlistOverview,
} from "@/lib/actions/watchlists";
import {
  tooltipClass,
  tooltipWarningClass,
} from "@/components/ui/tooltip-styles";

const MOBILE_ACTIONS_REVEAL_PX = 112;

function CompanyLogoStack({
  companies,
}: {
  companies: UserWatchlistActivityPreview["topCompanies"];
}) {
  if (companies.length === 0) return null;

  return (
    <span
      className="isolate flex h-7 shrink-0 -space-x-2 pr-2"
      role="img"
      aria-label={companies.map((company) => company.name).join(", ")}
    >
      {companies.map((company, index) => (
        <span
          key={company.id}
          title={company.name}
          className="relative inline-flex size-7 items-center justify-center overflow-hidden rounded-full border border-border-soft/70 bg-surface shadow-sm ring-2 ring-surface transition-transform duration-200 group-hover:-translate-y-0.5 motion-reduce:transition-none"
          style={{ zIndex: companies.length - index }}
        >
          {company.icon ? (
            <Image
              src={company.icon}
              alt=""
              fill
              sizes="28px"
              unoptimized
              className="object-contain p-0.5"
            />
          ) : (
            <Building2 size={14} className="text-muted" aria-hidden="true" />
          )}
        </span>
      ))}
    </span>
  );
}

export function WatchlistCard({
  watchlist,
  activity,
  activityPending = true,
  href,
  onShare,
  onDelete,
}: {
  watchlist: UserWatchlistOverview;
  activity: UserWatchlistActivityPreview | null;
  activityPending?: boolean;
  href: string;
  onShare?: () => Promise<void>;
  onDelete?: () => Promise<void>;
}) {
  const { t } = useLingui();
  const mobileShareRef = useRef<HTMLButtonElement>(null);
  const mobileActionsRef = useRef<HTMLDivElement>(null);
  const cardLinkRef = useRef<HTMLAnchorElement>(null);
  const activeDeleteTriggerRef = useRef<HTMLButtonElement | null>(null);
  const deleteSucceededRef = useRef(false);
  const shareResetRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const suppressCardClickRef = useRef(false);
  const gestureRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    startOffset: number;
    axis: "pending" | "horizontal" | "vertical";
  } | null>(null);
  const [mobileActionsOpen, setMobileActionsOpen] = useState(false);
  const [dragOffset, setDragOffset] = useState<number | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [shareState, setShareState] = useState<"idle" | "sharing" | "copied" | "error">("idle");
  const [shareSurface, setShareSurface] = useState<"mobile" | "desktop" | null>(null);
  const actionsId = `watchlist-actions-${watchlist.id}`;
  const hasActions = Boolean(onShare && onDelete);
  const mobileActionsVisible = mobileActionsOpen || (dragOffset ?? 0) > 0;

  useEffect(() => () => clearTimeout(shareResetRef.current), []);

  function setMobileActions(reveal: boolean, moveFocus = false) {
    setMobileActionsOpen(reveal);
    const closingFocusedActions = !reveal
      && mobileActionsRef.current?.contains(document.activeElement);
    if (moveFocus || closingFocusedActions) {
      requestAnimationFrame(() => {
        if (reveal) mobileShareRef.current?.focus();
        else cardLinkRef.current?.focus();
      });
    }
  }

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (
      !hasActions ||
      (typeof window.matchMedia === "function" &&
        !window.matchMedia("(max-width: 767px)").matches)
    ) {
      return;
    }
    gestureRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startOffset: mobileActionsOpen ? MOBILE_ACTIONS_REVEAL_PX : 0,
      axis: "pending",
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const gesture = gestureRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    const dx = event.clientX - gesture.startX;
    const dy = event.clientY - gesture.startY;
    if (gesture.axis === "pending" && Math.max(Math.abs(dx), Math.abs(dy)) > 6) {
      gesture.axis = Math.abs(dx) > Math.abs(dy) ? "horizontal" : "vertical";
    }
    if (gesture.axis !== "horizontal") return;
    event.preventDefault();
    setDragOffset(Math.max(
      0,
      Math.min(MOBILE_ACTIONS_REVEAL_PX, gesture.startOffset + dx),
    ));
  }

  function finishPointerGesture(event: React.PointerEvent<HTMLDivElement>) {
    const gesture = gestureRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    if (gesture.axis === "horizontal") {
      const dx = event.clientX - gesture.startX;
      const finalOffset = Math.max(
        0,
        Math.min(MOBILE_ACTIONS_REVEAL_PX, gesture.startOffset + dx),
      );
      setMobileActions(finalOffset >= MOBILE_ACTIONS_REVEAL_PX / 2);
      suppressCardClickRef.current = true;
      setTimeout(() => {
        suppressCardClickRef.current = false;
      }, 0);
    }
    setDragOffset(null);
    gestureRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  async function handleShare(surface: "mobile" | "desktop") {
    if (!onShare || shareState === "sharing") return;
    clearTimeout(shareResetRef.current);
    setShareSurface(surface);
    setShareState("sharing");
    try {
      await onShare();
      setShareState("copied");
      shareResetRef.current = setTimeout(() => {
        setShareState("idle");
        setShareSurface(null);
      }, 2_500);
    } catch {
      setShareState("error");
    }
  }

  async function handleDelete() {
    if (!onDelete || deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await onDelete();
      deleteSucceededRef.current = true;
      setDeleteOpen(false);
    } catch {
      setDeleteError(t({
        id: "watchlists.actions.deleteFailed",
        comment: "Error shown when deleting a watchlist from its overview card fails",
        message: "Could not delete this watchlist.",
      }));
    } finally {
      setDeleting(false);
    }
  }

  const shareLabel = shareState === "copied"
    ? t({ id: "watchlists.actions.copied", comment: "Confirmation after copying an unlisted watchlist link", message: "Link copied" })
    : shareState === "error"
      ? t({ id: "watchlists.actions.shareFailed", comment: "Error after an unlisted watchlist link cannot be copied", message: "Copy failed" })
      : t({ id: "watchlists.actions.share", comment: "Action to share a watchlist by unlisted link", message: "Share" });
  const deleteLabel = t({ id: "watchlists.actions.delete", comment: "Delete watchlist action", message: "Delete" });

  const actionButtons = (mobile: boolean) => {
    const sharing = shareState === "sharing";
    const shareButton = (
      <button
        ref={mobile ? mobileShareRef : undefined}
        type="button"
        onClick={() => void handleShare(mobile ? "mobile" : "desktop")}
        onFocus={() => {
          if (mobile) setMobileActions(true);
        }}
        aria-disabled={sharing}
        aria-busy={sharing}
        className={mobile
          ? `relative z-0 flex min-h-full w-14 shrink-0 flex-col items-center justify-center gap-1 rounded-l-xl bg-transparent px-2 text-[10px] font-medium text-foreground transition-colors focus-visible:z-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${sharing ? "cursor-wait" : "cursor-pointer"}`
          : `inline-flex size-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-border-soft hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${sharing ? "cursor-wait" : "cursor-pointer"}`}
        aria-label={shareLabel}
      >
        {shareState === "sharing" ? (
          <Loader2 size={mobile ? 18 : 15} className="motion-safe:animate-spin" aria-hidden="true" />
        ) : shareState === "copied" ? (
          <Check size={mobile ? 18 : 15} aria-hidden="true" />
        ) : (
          <Share2 size={mobile ? 18 : 15} aria-hidden="true" />
        )}
        {mobile ? <span>{shareLabel}</span> : null}
      </button>
    );

    const feedbackOpen = shareSurface === (mobile ? "mobile" : "desktop")
      && (shareState === "copied" || shareState === "error");

    return (
      <>
        <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
          <Tooltip.Root open={feedbackOpen}>
            <Tooltip.Trigger asChild>{shareButton}</Tooltip.Trigger>
            <Tooltip.Portal>
              <Tooltip.Content
                className={`${shareState === "copied" ? tooltipClass : tooltipWarningClass} ${mobile ? "md:hidden" : "hidden md:block"}`}
                side="top"
                sideOffset={7}
              >
                <span role="status" aria-live="polite">{shareLabel}</span>
              </Tooltip.Content>
            </Tooltip.Portal>
          </Tooltip.Root>
        </Tooltip.Provider>
      <button
        type="button"
        onClick={(event) => {
          activeDeleteTriggerRef.current = event.currentTarget;
          deleteSucceededRef.current = false;
          setDeleteError("");
          setDeleteOpen(true);
        }}
        onFocus={() => {
          if (mobile) setMobileActions(true);
        }}
        className={mobile
          ? "relative z-10 flex min-h-full w-14 shrink-0 cursor-pointer flex-col items-center justify-center gap-1 rounded-l-xl border-y border-l border-error-border/50 bg-[color-mix(in_srgb,var(--error-color)_8%,var(--background))] px-2 text-[10px] font-medium text-error transition-colors focus-visible:z-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-error"
          : "inline-flex size-8 cursor-pointer items-center justify-center rounded-md text-muted transition-colors hover:bg-error-bg hover:text-error focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-error"}
        aria-label={deleteLabel}
      >
        <Trash2 size={mobile ? 18 : 15} aria-hidden="true" />
        {mobile ? <span>{deleteLabel}</span> : null}
      </button>
      </>
    );
  };

  return (
    <div
      className="relative overflow-hidden rounded-xl md:overflow-visible"
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setMobileActionsOpen(false);
          setDragOffset(null);
        }
      }}
    >
      <div
        data-testid="watchlist-card-drag-surface"
        className={`relative z-20 min-w-0 ${dragOffset == null ? "transition-transform duration-200 motion-reduce:transition-none" : ""} md:!transform-none`}
        style={{
          transform: `translate3d(${dragOffset ?? (mobileActionsOpen ? MOBILE_ACTIONS_REVEAL_PX : 0)}px, 0, 0)`,
          touchAction: "pan-y",
        }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishPointerGesture}
        onPointerCancel={finishPointerGesture}
        onLostPointerCapture={() => {
          gestureRef.current = null;
          setDragOffset(null);
        }}
        onClickCapture={(event) => {
          if (suppressCardClickRef.current) {
            event.preventDefault();
            event.stopPropagation();
          }
        }}
      >
        <div className="relative min-w-0">
          <Link
            ref={cardLinkRef}
            href={href}
            prefetch={false}
            className="group grid min-h-28 w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-xl border border-border-soft bg-surface px-4 py-4 text-left transition-[border-color,background-color,box-shadow] hover:border-primary/30 hover:bg-[color-mix(in_srgb,var(--foreground)_3%,var(--surface))] hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background motion-reduce:transition-none md:pr-24"
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
        <span className="mt-3 flex min-h-7 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          {activity ? (
            <>
              <span className="inline-flex h-7 items-center">
                <CompanyLogoStack companies={activity.topCompanies} />
                <span>
                  {activity.activeCompanyCount} {activity.activeCompanyCount === 1
                    ? t({ id: "watchlists.card.companySingular", comment: "Singular active company count on a watchlist card", message: "company" })
                    : t({ id: "watchlists.card.companyPlural", comment: "Plural active company count on a watchlist card", message: "companies" })}
                </span>
              </span>
              <span aria-hidden="true">&middot;</span>
              <span>
                {activity.activeJobCount} {activity.activeJobCount === 1
                  ? t({ id: "watchlists.card.jobSingular", comment: "Singular job count on watchlist card", message: "job" })
                  : t({ id: "watchlists.card.jobPlural", comment: "Plural job count on watchlist card", message: "jobs" })}
              </span>
            </>
          ) : activityPending ? (
            <span className="flex h-7 items-center gap-2" aria-hidden="true">
              <span className="flex -space-x-2 pr-2">
                {Array.from({ length: 3 }, (_, index) => (
                  <span
                    key={index}
                    className="size-7 rounded-full bg-border-soft ring-2 ring-surface motion-safe:animate-pulse"
                  />
                ))}
              </span>
              <span className="h-3 w-20 rounded bg-border-soft motion-safe:animate-pulse" />
            </span>
          ) : (
            <>
              <span>
                &mdash; {t({ id: "watchlists.card.companyPlural", comment: "Plural active company count on a watchlist card", message: "companies" })}
              </span>
              <span aria-hidden="true">&middot;</span>
              <span>
                &mdash; {t({ id: "watchlists.card.jobPlural", comment: "Plural job count on watchlist card", message: "jobs" })}
              </span>
            </>
          )}
        </span>
            </span>
            <ChevronRight
              size={18}
              className="text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-foreground motion-reduce:transition-none md:hidden"
              aria-hidden="true"
            />
          </Link>
          {hasActions ? (
            <>
              <div className="absolute right-3 top-3 z-20 hidden items-center gap-1 rounded-lg border border-border-soft bg-background/90 p-1 shadow-sm backdrop-blur md:flex">
                {actionButtons(false)}
              </div>
            </>
          ) : null}
        </div>
      </div>

      {hasActions ? (
        <div
          ref={mobileActionsRef}
          id={actionsId}
          className={`absolute inset-y-0 left-0 z-0 flex w-[7.875rem] items-stretch overflow-hidden rounded-l-xl bg-[color-mix(in_srgb,var(--error-color)_8%,var(--background))] md:hidden ${mobileActionsVisible ? "opacity-100" : "pointer-events-none opacity-0"}`}
          onKeyDown={(event) => {
            if (event.key === "Escape") setMobileActions(false, true);
          }}
        >
          <span
            data-testid="watchlist-share-action-underlay"
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-0 w-[4.375rem] rounded-l-xl border-y border-l border-border-soft/70 bg-[color-mix(in_srgb,var(--foreground)_8%,var(--background))]"
          />
          <span
            data-testid="watchlist-delete-action-edge"
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-28 w-3.5 border-y border-error-border/50"
          />
          {actionButtons(true)}
        </div>
      ) : null}

      <AlertDialog.Root open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in motion-reduce:animate-none data-[state=open]:fade-in-0" />
          <AlertDialog.Content
            className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-soft bg-surface p-6 shadow-xl data-[state=open]:animate-in motion-reduce:animate-none data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              if (deleteSucceededRef.current) {
                document.getElementById("create-watchlist-button")?.focus();
              } else if (activeDeleteTriggerRef.current?.isConnected) {
                activeDeleteTriggerRef.current.focus();
              }
            }}
          >
            <AlertDialog.Title className="text-base font-semibold">
              <Trans id="watchlists.delete.title" comment="Delete watchlist confirmation title">
                Delete watchlist?
              </Trans>
            </AlertDialog.Title>
            <AlertDialog.Description className="mt-2 text-sm text-muted">
              <Trans id="watchlists.delete.description" comment="Delete watchlist confirmation description">
                This will permanently delete this watchlist and all its settings. This action cannot be undone.
              </Trans>
            </AlertDialog.Description>
            {deleteError ? <p className="mt-3 text-sm text-error" role="alert">{deleteError}</p> : null}
            <div className="mt-5 flex justify-end gap-2">
              <AlertDialog.Cancel asChild>
                <button className="cursor-pointer rounded-md border border-border-soft px-4 py-2 text-sm font-medium transition-colors hover:bg-border-soft">
                  <Trans id="watchlists.delete.cancel" comment="Cancel delete watchlist">Cancel</Trans>
                </button>
              </AlertDialog.Cancel>
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting}
                className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-warning-border bg-warning-bg px-4 py-2 text-sm font-medium text-warning transition-opacity hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {deleting ? <Loader2 size={14} className="motion-safe:animate-spin" aria-hidden="true" /> : null}
                <Trans id="watchlists.delete.confirm" comment="Confirm delete watchlist">Delete</Trans>
              </button>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </div>
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
  const limitCloseRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [limitOpen, setLimitOpen] = useState(false);

  useEffect(() => () => clearTimeout(limitCloseRef.current), []);

  const limitLabel = t({
    id: "watchlists.card.limitReached",
    comment: "Warning tooltip when the account-wide watchlist limit is reached",
    message: "Maximum of 10 watchlists reached",
  });

  const button = (
    <button
      id="create-watchlist-button"
      type="button"
      onClick={() => {
        if (disabled) {
          clearTimeout(limitCloseRef.current);
          setLimitOpen(true);
          limitCloseRef.current = setTimeout(() => setLimitOpen(false), 3_000);
          return;
        }
        if (!creating && !disabled) onClick();
      }}
      aria-disabled={disabled || creating}
      aria-label={disabled ? limitLabel : undefined}
      className={`flex min-h-16 w-full items-center justify-center gap-2 rounded-xl border border-dashed border-border-soft bg-surface px-4 py-3 text-muted transition-colors ${
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
      <Tooltip.Root
        open={limitOpen}
        onOpenChange={(open) => {
          clearTimeout(limitCloseRef.current);
          setLimitOpen(open);
        }}
      >
        <Tooltip.Trigger asChild>{button}</Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className={`${tooltipWarningClass} flex items-center gap-1.5`}
            side="top"
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
