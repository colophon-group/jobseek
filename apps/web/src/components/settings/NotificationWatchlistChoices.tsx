"use client";
import { useEffect, useRef, useState, useTransition } from "react";
import Link from "next/link";
import { Bell, BellOff, Eye, Funnel } from "lucide-react";
import { useRouter } from "next/navigation";
import { Trans, useLingui } from "@lingui/react/macro";
import { FilterPillsReadOnly } from "@/components/search/filter-pills-readonly";
import { useLocalePath } from "@/lib/useLocalePath";
import { setWatchlistNotificationMode } from "@/lib/actions/notifications";
import type { NotificationWatchlistSetting, WatchlistNotificationMode } from "@/lib/notifications/settings-contract";

function WatchlistChoice({ setting, paused }: { setting: NotificationWatchlistSetting; paused: boolean }) {
  const lp = useLocalePath();
  const { t } = useLingui();
  const router = useRouter();
  const [mode, setMode] = useState(setting.mode);
  const [busy, startTransition] = useTransition();
  const inFlight = useRef(false);
  const [status, setStatus] = useState<"saved" | "error" | "narrowing_unavailable" | "notifications_paused" | null>(null);
  useEffect(() => setMode(setting.mode), [setting.mode]);
  function change(next: WatchlistNotificationMode) {
    if (paused || inFlight.current) return;
    inFlight.current = true;
    setStatus(null);
    startTransition(async () => {
      try {
        const result = await setWatchlistNotificationMode(setting.id, next);
        if ("error" in result) {
          setStatus(result.error === "narrowing_unavailable" || result.error === "notifications_paused" ? result.error : "error");
          if (result.error === "notifications_paused") router.refresh();
          return;
        }
        setMode(result.mode);
        setStatus("saved");
        router.refresh();
      } catch { setStatus("error"); }
      finally { inFlight.current = false; }
    });
  }
  const showPrompt = Boolean(setting.prompt);
  return <div className="min-w-0 py-3" aria-busy={busy}>
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 flex-1">
        <Link id={`notification-title-${setting.id}`} href={lp(`/watchlists/${setting.id}`)} prefetch={false} title={setting.title} className="inline-flex max-w-full items-center gap-2 text-sm font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
          <Eye size={16} aria-hidden="true" className="shrink-0 text-muted" /><span className="min-w-0 truncate">{setting.title}</span>
        </Link>
      </div>
      <fieldset disabled={paused || busy} className="flex w-fit max-w-full shrink-0 overflow-hidden rounded-sm border border-divider bg-surface">
        <legend className="sr-only">{setting.title}</legend>
        {([
          { value: "off", Icon: BellOff, label: t({ id: "settings.notifications.off", comment: "Exclude this watchlist from the weekly email", message: "Off" }) },
          { value: "all", Icon: Bell, label: t({ id: "settings.notifications.all", comment: "Include all jobs matching the structured watchlist filters", message: "All results" }) },
          { value: "narrowed", Icon: Funnel, label: t({ id: "settings.notifications.narrowed", comment: "Include only accepted results from the saved prompt", message: "Narrowed" }) },
        ] as const).map(({ value, Icon, label }) => <label key={value} className="relative min-w-0 cursor-pointer border-l border-divider first-of-type:border-l-0">
          <input type="radio" name={`notification-mode-${setting.id}`} value={value} checked={mode === value}
            disabled={value === "narrowed" && !setting.narrowingAvailable}
            onChange={() => change(value)} className="peer sr-only" />
          <span className="flex h-full items-center justify-center gap-1 px-2 py-1 text-xs text-muted transition-colors hover:bg-border-soft peer-checked:bg-primary peer-checked:font-semibold peer-checked:text-primary-contrast peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:-outline-offset-2 peer-focus-visible:outline-primary peer-checked:peer-focus-visible:outline-primary-contrast peer-disabled:cursor-not-allowed peer-disabled:opacity-40">
            <Icon size={15} aria-hidden="true" /><span>{label}</span>
          </span>
        </label>)}
      </fieldset>
    </div>
    {setting.filterPreview && <div className="mt-2"><FilterPillsReadOnly {...setting.filterPreview} workMode={setting.filterPreview.filters.workMode} compact maxVisible={3} /></div>}
    {showPrompt && <div className="mt-2 flex items-start gap-2 text-xs leading-relaxed text-muted">
      <Funnel size={13} aria-hidden="true" className="mt-0.5 shrink-0" />
      <p id={`notification-prompt-${setting.id}`} className="min-w-0 whitespace-pre-wrap break-words">{setting.prompt}</p>
    </div>}
    {mode === "narrowed" && !setting.narrowingAvailable && <p className="mt-2 text-xs text-muted"><Trans id="settings.notifications.inactivePrompt" comment="Narrowed emails stay quiet until narrowing is re-enabled">Enable narrowing to resume this watchlist.</Trans></p>}
    {!setting.narrowingAvailable && <Link href={lp(`/watchlists/${setting.id}`)} prefetch={false} className="mt-2 inline-block text-xs text-muted underline underline-offset-4"><Trans id="settings.notifications.setupPrompt" comment="Link to add a saved narrow-results prompt">Set up narrowing</Trans></Link>}
    <p role="status" aria-live="polite" className={status ? "mt-2 text-xs" : "sr-only"}>
      {status === "saved" && <Trans id="settings.notifications.saved" comment="Pause setting save confirmation">Notification preference saved.</Trans>}
      {status === "error" && <Trans id="settings.notifications.error" comment="Pause setting save failure">Could not save your preference. Please try again.</Trans>}
      {status === "narrowing_unavailable" && <Trans id="settings.notifications.unavailable" comment="Save failure when narrowing is disabled or the subscription has expired">Enable narrowing with an active subscription on this watchlist, then try again.</Trans>}
      {status === "notifications_paused" && <Trans id="settings.notifications.resumeFirst" comment="Global pause prevents changes to individual watchlist choices">Resume email notifications above to change your watchlist choices.</Trans>}
    </p>
  </div>;
}

export function NotificationWatchlistChoices({ watchlists, paused }: { watchlists: NotificationWatchlistSetting[]; paused: boolean }) {
  const lp = useLocalePath();
  return <div className="mt-5 divide-y divide-divider border-y border-divider">
    {watchlists.map(setting => <WatchlistChoice key={setting.id} setting={setting} paused={paused} />)}
    {watchlists.length === 0 && <Link href={lp("/watchlists")} prefetch={false} className="my-4 inline-block text-sm text-primary underline underline-offset-4"><Trans id="settings.notifications.addWatchlist" comment="Empty notification settings link to watchlists">Create a watchlist to choose which jobs to receive.</Trans></Link>}
  </div>;
}
