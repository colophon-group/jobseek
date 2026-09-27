"use client";
import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { setNotificationsPaused } from "@/lib/actions/notifications";

export function NotificationSettings({ paused, verified }: { paused: boolean; verified: boolean }) {
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
  return <section id="notifications" aria-labelledby="notifications-heading" className="scroll-mt-24 rounded-xl border border-divider p-5 sm:p-6">
    <h2 id="notifications-heading" className="text-lg font-semibold"><Trans id="settings.notifications.title" comment="Weekly job email settings heading">Weekly email notifications</Trans></h2>
    <p className="mt-2 text-sm text-muted"><Trans id="settings.notifications.description" comment="Explain consolidated weekly opt-in job emails">Enable notifications on any watchlist to receive one weekly email with new matching jobs. Weeks with no matches stay quiet.</Trans></p>
    <div className="mt-5 flex items-start justify-between gap-5">
      <div>
        <p id="notifications-pause-label" className="font-medium"><Trans id="settings.notifications.pause" comment="Global override switch label">Pause all email notifications</Trans></p>
        <p id="notifications-pause-description" className="mt-1 max-w-xl text-sm text-muted"><Trans id="settings.notifications.override" comment="Global pause preserves individual watchlist settings and excludes account emails">Overrides every watchlist without changing your saved choices. Verification and password-reset emails still arrive.</Trans></p>
      </div>
      <button type="button" role="switch" aria-checked={value} aria-labelledby="notifications-pause-label" aria-describedby="notifications-pause-description" aria-disabled={busy} aria-busy={busy} onClick={toggle}
        className={`relative mt-1 h-7 w-12 shrink-0 cursor-pointer rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background ${value ? "border-primary bg-primary" : "border-divider bg-border-soft"} ${busy ? "opacity-60" : ""}`}>
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-surface shadow-sm transition-transform motion-reduce:transition-none ${value ? "left-0.5 translate-x-5" : "left-0.5"}`} />
      </button>
    </div>
    {value && <p className="mt-4 rounded-lg bg-border-soft p-3 text-sm"><Trans id="settings.notifications.paused" comment="Paused notification state and resume behavior">All job emails are paused. Turn this off to resume your saved watchlist choices. Jobs from the paused period will not be sent.</Trans></p>}
    {!verified && <p className="mt-4 text-sm text-muted"><Trans id="settings.notifications.verify" comment="Notification eligibility explanation">Verify your email address to receive job notifications.</Trans></p>}
    <p className="mt-3 text-sm" role="status" aria-live="polite">{error ? <Trans id="settings.notifications.error" comment="Pause setting save failure">Could not save your preference. Please try again.</Trans> : saved ? <Trans id="settings.notifications.saved" comment="Pause setting save confirmation">Notification preference saved.</Trans> : null}</p>
  </section>;
}
