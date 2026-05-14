"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Bell, BellOff, Trash2, Pencil, Copy, Loader2, Globe, Lock, AlertTriangle } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import {
  copyWatchlist,
  deleteWatchlist,
  toggleWatchlistAlerts,
  updateWatchlist,
} from "@/lib/actions/watchlists";
import { useLocalePath } from "@/lib/useLocalePath";
import { useSession } from "@/components/SessionProvider";
import { tooltipClass, tooltipWarningClass } from "@/components/ui/tooltip-styles";
import { UpgradeModal, useUpgradeModal } from "@/components/ui/upgrade-modal";

const iconBtnClass =
  "inline-flex items-center justify-center rounded-md p-1.5 text-muted hover:bg-border-soft hover:text-foreground transition-colors cursor-pointer";

function ActionButton({
  label,
  onClick,
  disabled,
  warning,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  warning?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <button
          type="button"
          onClick={onClick}
          className={`${iconBtnClass} ${disabled ? "opacity-40" : ""}`}
          aria-label={label}
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
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function WatchlistActionBar({
  watchlistId,
  isOwner,
  isPublic: initialIsPublic,
  alertsEnabled,
  isPaidPlan,
  limitReached,
  onEdit,
}: {
  watchlistId: string;
  isOwner: boolean;
  isPublic: boolean;
  alertsEnabled: boolean;
  isPaidPlan: boolean;
  limitReached: boolean;
  onEdit?: () => void;
}) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const { user, isLoggedIn } = useSession();
  const [busy, setBusy] = useState(false);
  const [isPublic, setIsPublic] = useState(initialIsPublic);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const upgrade = useUpgradeModal();

  async function handleCopy() {
    if (!isLoggedIn) {
      router.push(lp("/sign-in"));
      return;
    }
    if (limitReached) {
      upgrade.show(t({
        id: "upgrade.reason.mirrorLimit",
        comment: "Reason shown in upgrade modal when mirror limit reached",
        message: "You've reached your watchlist limit. Upgrade your plan to mirror more watchlists.",
      }));
      return;
    }
    setBusy(true);
    try {
      const result = await copyWatchlist(watchlistId);
      if ("slug" in result && user?.username) {
        router.push(lp(`/${user.username}/${result.slug}`));
      } else {
        router.refresh();
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    setBusy(true);
    try {
      await deleteWatchlist(watchlistId);
      router.push(lp("/watchlists"));
    } finally {
      setBusy(false);
    }
  }

  function handleToggleVisibility() {
    if (!isPaidPlan && isPublic) {
      upgrade.show(t({
        id: "upgrade.reason.makePrivate",
        comment: "Reason shown in upgrade modal when trying to make watchlist private",
        message: "Private watchlists are a paid feature. Upgrade to hide your watchlists from others.",
      }));
      return;
    }
    const next = !isPublic;
    setIsPublic(next);
    updateWatchlist({ watchlistId, isPublic: next });
  }

  function handleToggleAlerts() {
    if (!isPaidPlan) {
      upgrade.show(t({
        id: "upgrade.reason.alerts",
        comment: "Reason shown in upgrade modal when trying to enable alerts",
        message: "Email alerts are a paid feature. Upgrade to get notified when new jobs match your watchlist.",
      }));
      return;
    }
    setBusy(true);
    toggleWatchlistAlerts(watchlistId)
      .then(() => router.refresh())
      .finally(() => setBusy(false));
  }

  if (busy) {
    return (
      <div className="flex items-center gap-1">
        <Loader2 size={16} className="animate-spin text-muted" />
      </div>
    );
  }

  return (
    <>
      <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
        <div className="flex items-center gap-1">
          {isOwner ? (
            <>
              {onEdit && (
                <ActionButton
                  label={t({ id: "watchlists.actions.edit", comment: "Edit watchlist tooltip", message: "Edit" })}
                  onClick={onEdit}
                >
                  <Pencil size={16} />
                </ActionButton>
              )}
              <ActionButton
                label={
                  isPublic
                    ? t({ id: "watchlists.actions.makePrivate", comment: "Make watchlist private tooltip", message: "Make private" })
                    : t({ id: "watchlists.actions.makePublic", comment: "Make watchlist public tooltip", message: "Make public" })
                }
                onClick={handleToggleVisibility}
                disabled={!isPaidPlan && isPublic}
                warning={!isPaidPlan && isPublic}
              >
                {isPublic ? <Globe size={16} /> : <Lock size={16} />}
              </ActionButton>
              <ActionButton
                label={
                  alertsEnabled
                    ? t({ id: "watchlists.actions.disableAlerts", comment: "Disable alerts tooltip", message: "Disable alerts" })
                    : t({ id: "watchlists.actions.enableAlerts", comment: "Enable alerts tooltip", message: "Enable alerts" })
                }
                onClick={handleToggleAlerts}
                disabled={!isPaidPlan}
                warning={!isPaidPlan}
              >
                {alertsEnabled ? <BellOff size={16} /> : <Bell size={16} />}
              </ActionButton>
              <ActionButton
                label={t({ id: "watchlists.actions.mirror", comment: "Mirror watchlist tooltip", message: "Mirror" })}
                onClick={handleCopy}
                disabled={limitReached}
                warning={limitReached}
              >
                <Copy size={16} />
              </ActionButton>
              <AlertDialog.Root open={deleteOpen} onOpenChange={setDeleteOpen}>
                <AlertDialog.Trigger asChild>
                  <span>
                    <ActionButton
                      label={t({ id: "watchlists.actions.delete", comment: "Delete watchlist tooltip", message: "Delete" })}
                      onClick={() => setDeleteOpen(true)}
                    >
                      <Trash2 size={16} />
                    </ActionButton>
                  </span>
                </AlertDialog.Trigger>
                <AlertDialog.Portal>
                  <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
                  <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-soft bg-surface p-6 shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
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
                        <button className="rounded-md border border-border-soft px-4 py-2 text-sm font-medium transition-colors hover:bg-border-soft cursor-pointer">
                          <Trans id="watchlists.delete.cancel" comment="Cancel delete watchlist">
                            Cancel
                          </Trans>
                        </button>
                      </AlertDialog.Cancel>
                      <AlertDialog.Action asChild>
                        <button
                          onClick={handleDelete}
                          className="rounded-md border border-warning-border bg-warning-bg px-4 py-2 text-sm font-medium text-warning transition-opacity hover:opacity-80 cursor-pointer"
                        >
                          <Trans id="watchlists.delete.confirm" comment="Confirm delete watchlist">
                            Delete
                          </Trans>
                        </button>
                      </AlertDialog.Action>
                    </div>
                  </AlertDialog.Content>
                </AlertDialog.Portal>
              </AlertDialog.Root>
            </>
          ) : (
            <ActionButton
              label={
                isLoggedIn
                  ? t({ id: "watchlists.actions.mirror", comment: "Mirror watchlist tooltip", message: "Mirror" })
                  : t({ id: "watchlists.actions.mirrorSignIn", comment: "Mirror watchlist tooltip for non-logged-in users", message: "Sign in to mirror" })
              }
              onClick={handleCopy}
              disabled={isLoggedIn && limitReached}
              warning={isLoggedIn && limitReached}
            >
              <Copy size={16} />
            </ActionButton>
          )}
        </div>
      </Tooltip.Provider>
      <UpgradeModal open={upgrade.open} onOpenChange={upgrade.setOpen} reason={upgrade.reason} />
    </>
  );
}
