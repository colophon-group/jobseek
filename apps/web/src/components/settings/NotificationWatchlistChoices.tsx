"use client";
import { useEffect, useRef, useState, useTransition } from "react";
import Link from "next/link";
import { Bell, BellOff, Eye, Funnel } from "lucide-react";
import { useRouter } from "next/navigation";
import { Trans, useLingui } from "@lingui/react/macro";
import * as Dialog from "@radix-ui/react-dialog";
import { SettingsDialog } from "./SettingsDialog";
import { SettingsChoice } from "./SettingsChoice";
import { FilterPillsReadOnly } from "@/components/search/filter-pills-readonly";
import { useLocalePath } from "@/lib/useLocalePath";
import { setWatchlistNotificationMode } from "@/lib/actions/notifications";
import type {
  NotificationWatchlistSetting,
  WatchlistNotificationMode,
} from "@/lib/notifications/settings-contract";

function WatchlistChoice({
  setting,
  paused,
}: {
  setting: NotificationWatchlistSetting;
  paused: boolean;
}) {
  const lp = useLocalePath();
  const { t } = useLingui();
  const router = useRouter();
  const [mode, setMode] = useState(setting.mode);
  const [busy, startTransition] = useTransition();
  const inFlight = useRef(false);
  const [status, setStatus] = useState<
    "saved" | "error" | "narrowing_unavailable" | "notifications_paused" | null
  >(null);
  useEffect(() => setMode(setting.mode), [setting.mode]);
  function change(next: WatchlistNotificationMode) {
    if (paused || inFlight.current) return;
    inFlight.current = true;
    setStatus(null);
    startTransition(async () => {
      try {
        const result = await setWatchlistNotificationMode(setting.id, next);
        if ("error" in result) {
          setStatus(
            result.error === "narrowing_unavailable" ||
              result.error === "notifications_paused"
              ? result.error
              : "error",
          );
          if (result.error === "notifications_paused") router.refresh();
          return;
        }
        setMode(result.mode);
        setStatus("saved");
        router.refresh();
      } catch {
        setStatus("error");
      } finally {
        inFlight.current = false;
      }
    });
  }
  const showPrompt = Boolean(setting.prompt);
  return (
    <div className="min-w-0 py-2" aria-busy={busy}>
      <div className="flex items-start justify-between gap-3 sm:items-center">
        <div className="min-w-0 flex-1">
          <Link
            id={`notification-title-${setting.id}`}
            href={lp(`/watchlists/${setting.id}`)}
            prefetch={false}
            title={setting.title}
            className="inline-flex max-w-full items-center gap-2 text-sm font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <Eye size={16} aria-hidden="true" className="shrink-0 text-muted" />
            <span className="min-w-0 break-words">{setting.title}</span>
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-x-3">
            {setting.filterPreview && (
              <FilterPillsReadOnly
                {...setting.filterPreview}
                workMode={setting.filterPreview.filters.workMode}
                compact
                maxVisible={2}
              />
            )}
            {showPrompt && (
              <Dialog.Root>
                <Dialog.Trigger className="min-h-11 text-[11px] text-muted underline underline-offset-4">
                  <Trans
                    id="settings.notifications.filters"
                    comment="Open the full natural-language narrowing prompt"
                  >
                    Narrowing filters
                  </Trans>
                </Dialog.Trigger>
                <SettingsDialog
                  title={t({
                    id: "settings.notifications.filters",
                    comment: "Open the full natural-language narrowing prompt",
                    message: "Narrowing filters",
                  })}
                  description={setting.title}
                >
                  <p
                    id={`notification-prompt-${setting.id}`}
                    className="whitespace-pre-wrap break-words border-l border-divider pl-3 text-sm leading-relaxed"
                  >
                    {setting.prompt}
                  </p>
                </SettingsDialog>
              </Dialog.Root>
            )}
          </div>
        </div>
        <div className="shrink-0">
          <SettingsChoice
            label={setting.title}
            value={mode}
            disabled={paused || busy}
            onChange={(next) => change(next as WatchlistNotificationMode)}
            options={[
              {
                value: "off",
                icon: <BellOff size={14} aria-hidden="true" />,
                label: t({
                  id: "settings.notifications.off",
                  comment: "Exclude this watchlist from the weekly email",
                  message: "Off",
                }),
                description: t({
                  id: "settings.notifications.offHelp",
                  comment: "Explanation in the per-watchlist email mode menu",
                  message: "No jobs from this watchlist.",
                }),
              },
              {
                value: "all",
                icon: <Bell size={14} aria-hidden="true" />,
                label: t({
                  id: "settings.notifications.all",
                  comment:
                    "Include all jobs matching the structured watchlist filters",
                  message: "All results",
                }),
                description: t({
                  id: "settings.notifications.allHelp",
                  comment: "Explanation in the per-watchlist email mode menu",
                  message: "All jobs matching this watchlist.",
                }),
              },
              {
                value: "narrowed",
                icon: <Funnel size={14} aria-hidden="true" />,
                label: t({
                  id: "settings.notifications.narrowed",
                  comment:
                    "Include only accepted results from the saved prompt",
                  message: "Narrowed",
                }),
                disabled: !setting.narrowingAvailable,
                description: t({
                  id: "settings.notifications.narrowedOptionHelp",
                  comment:
                    "Explanation in the per-watchlist email mode menu; eligibility still applies",
                  message: "Only jobs matching your narrowing filters.",
                }),
              },
            ]}
          />
        </div>
      </div>
      {mode === "narrowed" && !setting.narrowingAvailable && (
        <p className="mt-2 text-xs text-muted">
          <Trans
            id="settings.notifications.inactivePrompt"
            comment="Narrowed emails stay quiet until narrowing is re-enabled"
          >
            Enable narrowing to resume this watchlist.
          </Trans>
        </p>
      )}
      {!setting.narrowingAvailable && (
        <Link
          href={lp(`/watchlists/${setting.id}`)}
          prefetch={false}
          className="mt-2 inline-block text-xs text-muted underline underline-offset-4"
        >
          <Trans
            id="settings.notifications.setupPrompt"
            comment="Link to add a saved narrow-results prompt"
          >
            Set up narrowing
          </Trans>
        </Link>
      )}
      <p
        role="status"
        aria-live="polite"
        className={status ? "mt-2 text-xs" : "sr-only"}
      >
        {status === "saved" && (
          <Trans
            id="settings.notifications.saved"
            comment="Pause setting save confirmation"
          >
            Notification preference saved.
          </Trans>
        )}
        {status === "error" && (
          <Trans
            id="settings.notifications.error"
            comment="Pause setting save failure"
          >
            Could not save your preference. Please try again.
          </Trans>
        )}
        {status === "narrowing_unavailable" && (
          <Trans
            id="settings.notifications.unavailable"
            comment="Save failure when narrowing is disabled or the subscription has expired"
          >
            Enable narrowing with an active subscription on this watchlist, then
            try again.
          </Trans>
        )}
        {status === "notifications_paused" && (
          <Trans
            id="settings.notifications.resumeFirst"
            comment="Global pause prevents changes to individual watchlist choices"
          >
            Resume email notifications above to change your watchlist choices.
          </Trans>
        )}
      </p>
    </div>
  );
}

export function NotificationWatchlistChoices({
  watchlists,
  paused,
}: {
  watchlists: NotificationWatchlistSetting[];
  paused: boolean;
}) {
  const lp = useLocalePath();
  return (
    <div className="mt-5 divide-y divide-divider border-y border-divider">
      {watchlists.map((setting) => (
        <WatchlistChoice key={setting.id} setting={setting} paused={paused} />
      ))}
      {watchlists.length === 0 && (
        <Link
          href={lp("/watchlists")}
          prefetch={false}
          className="my-4 inline-block text-sm text-primary underline underline-offset-4"
        >
          <Trans
            id="settings.notifications.addWatchlist"
            comment="Empty notification settings link to watchlists"
          >
            Create a watchlist to choose which jobs to receive.
          </Trans>
        </Link>
      )}
    </div>
  );
}
