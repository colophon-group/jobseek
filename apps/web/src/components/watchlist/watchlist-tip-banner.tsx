"use client";

import { useState, useEffect } from "react";
import { Trans, useLingui } from "@lingui/react/macro";
import { Lightbulb } from "lucide-react";
import { usePathname } from "next/navigation";
import { useBanner } from "@/components/BannerProvider";
import { updatePreferences } from "@/lib/actions/preferences";

const BANNER_ID = "watchlist-tip-dismissed";

export function WatchlistTipBanner({ aboveBottomBar }: { aboveBottomBar?: boolean }) {
  const { t } = useLingui();
  const pathname = usePathname();
  const { activeBanner, claim, dismiss: dismissBanner } = useBanner();
  const [claimed, setClaimed] = useState(false);

  // Only show on watchlist-related pages (watchlists list + public watchlist view)
  // Exclude known app routes that also match /:lang/:x/:y (e.g. /en/company/apple)
  const APP_ROUTES = /^\/(company|explore|saved|settings|progress)\//;
  const segments = pathname.replace(/^\/[a-z]{2}\//, ""); // strip locale prefix
  const isWatchlistPage = pathname.includes("/watchlists") ||
    (/^\/[a-z]{2}\/[^/]+\/[^/]+$/.test(pathname) && !APP_ROUTES.test("/" + segments));

  useEffect(() => {
    if (!isWatchlistPage) return;
    if (localStorage.getItem(BANNER_ID)) return;
    if (claim(BANNER_ID)) setClaimed(true);
  }, [isWatchlistPage, claim]);

  if (!claimed || !isWatchlistPage || activeBanner !== BANNER_ID) return null;

  function dismiss() {
    localStorage.setItem(BANNER_ID, "1");
    dismissBanner(BANNER_ID);
    setClaimed(false);
    updatePreferences({ dismissBanner: BANNER_ID }).catch(() => {});
  }

  return (
    <div className={aboveBottomBar
      ? "fixed bottom-[52px] left-0 right-0 z-50 border-t border-info-border bg-info-bg dark:bg-[rgba(147,187,253,0.15)] backdrop-blur-sm md:fixed md:top-12 md:bottom-auto md:z-40 md:border-b md:border-t-0"
      : "border-b border-info-border bg-info-bg dark:bg-[rgba(147,187,253,0.15)] backdrop-blur-sm"
    }>
      <div className="mx-auto flex max-w-[1200px] flex-col gap-2 px-4 py-2 text-sm text-info sm:flex-row sm:items-center sm:gap-3">
        <div className="flex flex-1 items-start gap-2">
          <Lightbulb size={16} className="mt-0.5 shrink-0" />
          <p>
            <Trans
              id="watchlists.tip.mirror"
              comment="Tip banner explaining that public watchlists can be mirrored"
            >
              Mirror any public watchlist to make it your own — tweak companies, adjust filters, or enable alerts.
            </Trans>
          </p>
        </div>
        <button
          onClick={dismiss}
          className="shrink-0 self-end rounded-full bg-info-border px-2.5 py-1 font-medium transition-colors hover:opacity-80 cursor-pointer sm:self-auto"
        >
          {t({ id: "watchlists.tip.dismiss", comment: "Dismiss tip banner button", message: "Got it" })}
        </button>
      </div>
    </div>
  );
}
