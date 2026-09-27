"use client";
import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Bell } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { setNotificationsPaused } from "@/lib/actions/notifications";

import { NotificationWatchlistChoices } from "./NotificationWatchlistChoices";
import type { NotificationWatchlistSetting } from "@/lib/notifications/settings-contract";

export function NotificationSettings({ paused, verified, watchlists = [] }: { paused: boolean; verified: boolean; watchlists?: NotificationWatchlistSetting[] }) {
  const router = useRouter();
  const [value, setValue] = useState(paused);
  const [busy, startTransition] = useTransition();
  const inFlight = useRef(false);
  const [error, setError] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => setValue(paused), [paused]);
  function toggle() {
    if (inFlight.current) return;
    inFlight.current = true;
    setError(false);
    setSaved(false);
    startTransition(async () => {
      try {
        const result = await setNotificationsPaused(!value);
        if ("error" in result) throw new Error(result.error);
        setValue(result.notificationsPaused);
        setSaved(true);
        router.refresh();
      } catch { setError(true); }
      finally { inFlight.current = false; }
    });
  }
  return <section id="notifications" aria-labelledby="notifications-heading" className="scroll-mt-24">
    <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
      <h2 id="notifications-heading" className="flex items-center gap-2 text-lg font-semibold"><Bell size={19} aria-hidden="true" className="shrink-0 text-muted" /><Trans id="settings.notifications.title" comment="Weekly job email settings heading">Weekly email</Trans></h2>
      <label className="flex w-full shrink-0 items-center justify-between gap-2 text-sm sm:w-auto sm:justify-start">
        <span id="notifications-pause-label"><Trans id="settings.notifications.pause" comment="Global override switch label">Pause all</Trans></span>
        <button type="button" role="switch" aria-checked={value} aria-labelledby="notifications-pause-label" aria-disabled={busy} aria-busy={busy} onClick={toggle}
          className={`relative h-6 w-10 shrink-0 cursor-pointer rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background ${value ? "border-primary bg-primary" : "border-divider bg-border-soft"} ${busy ? "opacity-60" : ""}`}>
          <span className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-surface shadow-sm transition-transform motion-reduce:transition-none ${value ? "translate-x-4" : ""}`} />
        </button>
      </label>
    </div>
    <p className="mt-2 text-sm text-muted"><Trans id="settings.notifications.description" comment="Explain consolidated weekly opt-in job emails">One email with results from all your enabled watchlists.</Trans></p>
    {value && <p className="mt-4 rounded-lg bg-border-soft px-3 py-2 text-sm"><Trans id="settings.notifications.paused" comment="Paused notification state and resume behavior">Paused. Your watchlist choices are saved.</Trans></p>}
    <NotificationWatchlistChoices watchlists={watchlists} paused={value} />
    {!verified && <p className="mt-4 text-sm text-muted"><Trans id="settings.notifications.verify" comment="Notification eligibility explanation">Verify your email address to receive job notifications.</Trans></p>}
    <p className={error || saved ? "mt-3 text-sm" : "sr-only"} role="status" aria-live="polite">{error ? <Trans id="settings.notifications.error" comment="Pause setting save failure">Could not save your preference. Please try again.</Trans> : saved ? <Trans id="settings.notifications.saved" comment="Pause setting save confirmation">Notification preference saved.</Trans> : null}</p>
    <details className="mt-5 text-xs text-muted">
      <summary className="w-fit cursor-pointer rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><Trans id="settings.notifications.how" comment="Collapsed explanation of weekly email behavior">How weekly emails work</Trans></summary>
      <div className="mt-3 space-y-2 leading-relaxed">
        <p><Trans id="settings.notifications.deliveryHelp" comment="Expanded explanation of combined delivery and pause behavior">Duplicate jobs appear once. No matches, no email. Resuming starts fresh, without jobs from the paused period.</Trans></p>
        <p><Trans id="settings.notifications.narrowedHelp" comment="Expanded explanation of narrowed email selection">Narrowed results use your saved prompt. Emails wait for evaluation to finish. If narrowing is unavailable, that watchlist stays quiet.</Trans></p>
        <p><Trans id="settings.notifications.override" comment="Expanded explanation of global pause and account emails">Pausing keeps your choices. Verification and password-reset emails still arrive.</Trans></p>
      </div>
    </details>
  </section>;
}
