"use client";

import { useState, useEffect } from "react";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import { updatePreferences } from "@/lib/actions/preferences";
import { useBanner } from "@/components/BannerProvider";
import { NavLink } from "@/components/NavLink";
import { Info } from "lucide-react";

const BANNER_ID = "cookie-consent";

type CookieBannerProps = {
  aboveBottomBar?: boolean;
  serverConsent?: boolean;
};

export function CookieBanner({ aboveBottomBar, serverConsent }: CookieBannerProps) {
  const { t } = useLingui();
  const lp = useLocalePath();
  const { activeBanner, claim, dismiss: dismissBanner } = useBanner();
  const [claimed, setClaimed] = useState(false);

  useEffect(() => {
    if (serverConsent) return;
    if (localStorage.getItem(BANNER_ID)) return;
    if (claim(BANNER_ID)) setClaimed(true);
  }, [serverConsent, claim]);

  if (!claimed || activeBanner !== BANNER_ID) return null;

  function dismiss() {
    localStorage.setItem(BANNER_ID, "1");
    dismissBanner(BANNER_ID);
    setClaimed(false);
    updatePreferences({ cookieConsent: true, dismissBanner: BANNER_ID }).catch(() => {});
  }

  return (
    <div className={aboveBottomBar
      ? "fixed bottom-[52px] left-0 right-0 z-50 border-t border-info-border bg-info-bg dark:bg-[rgba(147,187,253,0.15)] backdrop-blur-sm md:fixed md:top-12 md:bottom-auto md:z-40 md:border-b md:border-t-0"
      : "border-b border-info-border bg-info-bg dark:bg-[rgba(147,187,253,0.15)] backdrop-blur-sm"
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
          <NavLink
            href={lp("/privacy-policy")}
            prefetch={false}
            className="rounded-full px-2.5 py-1 font-medium transition-colors hover:bg-info-border"
          >
            {t({ id: "common.cookies.details", comment: "Cookie banner details button", message: "Details" })}
          </NavLink>
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
