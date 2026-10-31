"use client";

import { useEffect, useState } from "react";
import { Trans } from "@lingui/react/macro";
import { Heart } from "lucide-react";
import { Button } from "@/components/ui/Button";

const DISMISSAL_KEY = "service-shutdown-notice-v1";

export function ServiceShutdownNotice() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      setVisible(localStorage.getItem(DISMISSAL_KEY) !== "1");
    } catch {
      setVisible(true);
    }
  }, []);

  function dismiss() {
    setVisible(false);
    try {
      localStorage.setItem(DISMISSAL_KEY, "1");
    } catch {
      // Dismissal still works for this visit when browser storage is unavailable.
    }
  }

  if (!visible) return null;

  return (
    <aside
      aria-labelledby="service-shutdown-notice-title"
      data-service-shutdown-notice
      className="fixed inset-x-3 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-40 mx-auto max-h-[calc(100dvh-6rem)] max-w-3xl overflow-y-auto rounded-xl border border-border-soft bg-surface p-4 text-foreground shadow-xl md:bottom-5"
    >
      <div className="flex items-start gap-3">
        <Heart size={20} aria-hidden="true" className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <h2 id="service-shutdown-notice-title" className="text-sm font-semibold">
            <Trans id="common.shutdown.title" comment="Friendly heading for the dismissible site-wide service shutdown notice">
              A quick heads-up
            </Trans>
          </h2>
          <p className="mt-1 text-sm leading-relaxed text-muted-strong">
            <Trans id="common.shutdown.message" comment="Shutdown notice for all visitors: thank them and promise one free month on an unnamed future service. Do not add a shutdown date, service name or product details.">
              Jobseek is going to shut down. Thank you for being here — it means a lot. We’ll give you one month free on our next service when it’s ready.
            </Trans>
          </p>
          <div className="mt-3 flex justify-end">
            <Button type="button" size="sm" onClick={dismiss} className="min-h-11 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-surface">
              <Trans id="common.shutdown.dismiss" comment="Button that dismisses only the shutdown notice, without changing cookie or marketing consent">
                Got it
              </Trans>
            </Button>
          </div>
        </div>
      </div>
    </aside>
  );
}
