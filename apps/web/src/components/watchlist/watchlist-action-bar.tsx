"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Bell,
  BellOff,
  Check,
  Loader2,
  Pencil,
  Share2,
  Trash2,
} from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import {
  deleteWatchlist,
  shareWatchlist,
  toggleWatchlistAlerts,
} from "@/lib/actions/watchlists";
import { tooltipClass, tooltipWarningClass } from "@/components/ui/tooltip-styles";
import { useLocalePath } from "@/lib/useLocalePath";
import { copyTextToClipboard } from "@/lib/copy-text-to-clipboard";

const iconBtnClass =
  "inline-flex items-center justify-center rounded-md p-1.5 text-muted hover:bg-border-soft hover:text-foreground transition-colors cursor-pointer";

function ActionButton({
  label,
  onClick,
  warning,
  tooltipOpen,
  announce,
  busy,
  buttonRef,
  children,
}: {
  label: string;
  onClick: () => void;
  warning?: boolean;
  tooltipOpen?: boolean;
  announce?: boolean;
  busy?: boolean;
  buttonRef?: React.Ref<HTMLButtonElement>;
  children: React.ReactNode;
}) {
  const [tooltipRequestedOpen, setTooltipRequestedOpen] = useState(false);
  return (
    <Tooltip.Root
      open={tooltipOpen === true || tooltipRequestedOpen}
      onOpenChange={setTooltipRequestedOpen}
    >
      <Tooltip.Trigger asChild>
        <button
          ref={buttonRef}
          type="button"
          onClick={onClick}
          className={`${iconBtnClass} ${busy ? "cursor-wait" : ""}`}
          aria-label={label}
          aria-busy={busy || undefined}
          aria-disabled={busy || undefined}
        >
          {children}
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          className={`${warning ? tooltipWarningClass : tooltipClass} flex items-center gap-1.5`}
          sideOffset={6}
        >
          {warning && <AlertTriangle size={12} className="shrink-0" />}
          {announce ? <span role="status" aria-live="polite">{label}</span> : label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function WatchlistActionBar({
  watchlistId,
  alertsEnabled,
  onEdit,
}: {
  watchlistId: string;
  alertsEnabled: boolean;
  onEdit?: () => void;
}) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [alertsBusy, setAlertsBusy] = useState(false);
  const [displayAlertsEnabled, setDisplayAlertsEnabled] = useState(alertsEnabled);
  const [alertsError, setAlertsError] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [shareState, setShareState] = useState<"idle" | "sharing" | "copied" | "error">("idle");
  const deleteButtonRef = useRef<HTMLButtonElement>(null);
  const shareResetRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const alertsResetRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const alertsMutationInFlightRef = useRef(false);

  useEffect(() => {
    setDisplayAlertsEnabled(alertsEnabled);
  }, [alertsEnabled]);

  useEffect(() => () => {
    clearTimeout(shareResetRef.current);
    clearTimeout(alertsResetRef.current);
  }, []);

  async function handleShare() {
    if (shareState === "sharing") return;
    clearTimeout(shareResetRef.current);
    setShareState("sharing");
    try {
      const result = await shareWatchlist(watchlistId);
      if ("error" in result) throw new Error(result.error);
      await copyTextToClipboard(result.url);
      setShareState("copied");
      shareResetRef.current = setTimeout(() => setShareState("idle"), 2_500);
    } catch {
      setShareState("error");
    }
  }

  async function handleDelete() {
    setDeleteBusy(true);
    setDeleteError("");
    try {
      const result = await deleteWatchlist(watchlistId);
      if (!result.ok) {
        setDeleteError(t({
          id: "watchlists.actions.deleteFailed",
          comment: "Error shown when deleting a watchlist fails",
          message: "Could not delete this watchlist.",
        }));
        return;
      }
      router.replace(lp("/watchlists"));
      router.refresh();
    } catch {
      setDeleteError(t({
        id: "watchlists.actions.deleteFailed",
        comment: "Error shown when deleting a watchlist fails",
        message: "Could not delete this watchlist.",
      }));
    } finally {
      setDeleteBusy(false);
    }
  }

  async function handleToggleAlerts() {
    if (alertsMutationInFlightRef.current) return;
    alertsMutationInFlightRef.current = true;
    clearTimeout(alertsResetRef.current);
    setAlertsError(false);
    setAlertsBusy(true);
    try {
      const result = await toggleWatchlistAlerts(watchlistId);
      if ("error" in result) {
        throw new Error(result.error);
      }
      setDisplayAlertsEnabled(result.enabled);
      router.refresh();
    } catch {
      setAlertsError(true);
      alertsResetRef.current = setTimeout(() => setAlertsError(false), 2_500);
    } finally {
      alertsMutationInFlightRef.current = false;
      setAlertsBusy(false);
    }
  }

  if (deleteBusy) {
    return (
      <div className="flex items-center gap-1">
        <Loader2 size={16} className="motion-safe:animate-spin text-muted" />
      </div>
    );
  }

  const shareLabel = shareState === "copied"
    ? t({ id: "watchlists.actions.copied", comment: "Confirmation after copying an unlisted watchlist link", message: "Link copied" })
    : shareState === "error"
      ? t({ id: "watchlists.actions.shareFailed", comment: "Error after an unlisted watchlist link cannot be copied", message: "Copy failed" })
      : t({ id: "watchlists.actions.share", comment: "Action to share a watchlist by unlisted link", message: "Share" });
  const alertsLabel = alertsError
    ? t({ id: "watchlists.actions.alertsFailed", comment: "Error after a watchlist alert preference cannot be updated", message: "Could not update alerts" })
    : displayAlertsEnabled
      ? t({ id: "watchlists.actions.disableAlerts", comment: "Disable alerts tooltip", message: "Disable alerts" })
      : t({ id: "watchlists.actions.enableAlerts", comment: "Enable alerts tooltip", message: "Enable alerts" });

  return (
    <>
      <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
        <div className="flex items-center gap-1">
          <>
            {onEdit && (
              <ActionButton
                label={t({ id: "watchlists.actions.edit", comment: "Edit watchlist tooltip", message: "Edit" })}
                onClick={onEdit}
              >
                <Pencil size={16} aria-hidden="true" />
              </ActionButton>
            )}
            <ActionButton
              label={shareLabel}
              onClick={() => void handleShare()}
              warning={shareState === "error"}
              tooltipOpen={shareState === "copied" || shareState === "error" ? true : undefined}
              announce={shareState === "copied" || shareState === "error"}
            >
              {shareState === "sharing" ? (
                <Loader2 size={16} className="motion-safe:animate-spin" aria-hidden="true" />
              ) : shareState === "copied" ? (
                <Check size={16} aria-hidden="true" />
              ) : (
                <Share2 size={16} aria-hidden="true" />
              )}
            </ActionButton>
            <ActionButton
              label={alertsLabel}
              onClick={() => void handleToggleAlerts()}
              busy={alertsBusy}
              warning={alertsError}
              tooltipOpen={alertsError ? true : undefined}
              announce={alertsError}
            >
              {alertsBusy ? (
                <Loader2 size={16} className="motion-safe:animate-spin" aria-hidden="true" />
              ) : displayAlertsEnabled ? (
                <BellOff size={16} aria-hidden="true" />
              ) : (
                <Bell size={16} aria-hidden="true" />
              )}
            </ActionButton>
            <AlertDialog.Root open={deleteOpen} onOpenChange={setDeleteOpen}>
              <ActionButton
                label={t({ id: "watchlists.actions.delete", comment: "Delete watchlist tooltip", message: "Delete" })}
                onClick={() => setDeleteOpen(true)}
                buttonRef={deleteButtonRef}
              >
                <Trash2 size={16} aria-hidden="true" />
              </ActionButton>
              <AlertDialog.Portal>
                <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in motion-reduce:animate-none data-[state=open]:fade-in-0" />
                <AlertDialog.Content
                  className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-soft bg-surface p-6 shadow-xl data-[state=open]:animate-in motion-reduce:animate-none data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
                  onCloseAutoFocus={(event) => {
                    event.preventDefault();
                    deleteButtonRef.current?.focus();
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
                  <div className="mt-5 flex justify-end gap-2">
                    <AlertDialog.Cancel asChild>
                      <button className="cursor-pointer rounded-md border border-border-soft px-4 py-2 text-sm font-medium transition-colors hover:bg-border-soft">
                        <Trans id="watchlists.delete.cancel" comment="Cancel delete watchlist">Cancel</Trans>
                      </button>
                    </AlertDialog.Cancel>
                    <AlertDialog.Action asChild>
                      <button
                        onClick={handleDelete}
                        className="cursor-pointer rounded-md border border-warning-border bg-warning-bg px-4 py-2 text-sm font-medium text-warning transition-opacity hover:opacity-80"
                      >
                        <Trans id="watchlists.delete.confirm" comment="Confirm delete watchlist">Delete</Trans>
                      </button>
                    </AlertDialog.Action>
                  </div>
                </AlertDialog.Content>
              </AlertDialog.Portal>
            </AlertDialog.Root>
          </>
        </div>
      </Tooltip.Provider>
      {deleteError ? <span className="text-xs text-error" role="alert">{deleteError}</span> : null}
    </>
  );
}
