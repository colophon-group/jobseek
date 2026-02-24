"use client";

import { useState, useEffect } from "react";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import { updatePreferences } from "@/lib/actions/preferences";
import Link from "next/link";
import { Info } from "lucide-react";

const STORAGE_KEY = "cookie-consent";

type CookieBannerProps = {
  /** When true, fixes the banner above a mobile bottom bar */
  aboveBottomBar?: boolean;
  /** Server-provided consent state from user preferences DB */
  serverConsent?: boolean;
};

export function CookieBanner({ aboveBottomBar, serverConsent }: CookieBannerProps) {
  const { t } = useLingui();
  const lp = useLocalePath();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (serverConsent) return;
    if (!localStorage.getItem(STORAGE_KEY)) {
      setVisible(true);
    }
  }, [serverConsent]);

  if (!visible) return null;

  function dismiss() {
    localStorage.setItem(STORAGE_KEY, "1");
    setVisible(false);
    updatePreferences({ cookieConsent: true }).catch(() => {});
  }

  return (
    <div className={aboveBottomBar
      ? "fixed bottom-[52px] left-0 right-0 z-50 border-t border-info-border bg-info-bg md:static md:bottom-auto md:z-auto md:border-b md:border-t-0"
      : "border-b border-info-border bg-info-bg"
    }>
      <div className="mx-auto flex max-w-[1200px] flex-col gap-2 px-4 py-2 text-sm text-info sm:flex-row sm:items-center sm:gap-3">
        <div className="flex flex-1 items-start gap-2">
          <Info size={16} className="mt-0.5 shrink-0" />
          <p>
            <Trans id="common.cookies.message" comment="Cookie banner message">
              This site uses cookies only for authentication and essential functionality.
            </Trans>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 self-end sm:self-auto">
          <Link
            href={lp("/privacy-policy")}
            prefetch={false}
            className="rounded-full px-2.5 py-1 font-medium transition-colors hover:bg-info-border"
          >
            {t({ id: "common.cookies.details", comment: "Cookie banner details button", message: "Details" })}
          </Link>
          <button
            onClick={dismiss}
            className="rounded-full bg-info-border px-2.5 py-1 font-medium transition-colors hover:opacity-80 cursor-pointer"
          >
            {t({ id: "common.cookies.ok", comment: "Cookie banner accept button", message: "Ok" })}
          </button>
        </div>
      </div>
    </div>
  );
}
