"use client";

import { useEffect, useState } from "react";
import { Crown, X } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useSession } from "@/components/providers/SessionProvider";
import { useBanner } from "@/components/providers/BannerProvider";
import { NavLink } from "@/components/NavLink";
import { useLocalePath } from "@/lib/useLocalePath";
import { updatePreferences } from "@/lib/actions/preferences";

const BANNER_ID = "pro-launch-v1";

export function ProAnnouncementBanner({ aboveBottomBar = false }: { aboveBottomBar?: boolean }) {
  const { user, plan, preferences, isPending } = useSession();
  const { activeBanner, claim, dismiss } = useBanner();
  const { t } = useLingui();
  const lp = useLocalePath();
  const [locallyDismissedFor, setLocallyDismissedFor] = useState<string | null>(null);
  const serverDismissed = preferences?.dismissedBanners?.includes(BANNER_ID) ?? false;

  useEffect(() => {
    if (!user || isPending || plan !== "free" || serverDismissed || locallyDismissedFor === user.id) return;
    try {
      if (localStorage.getItem(`${BANNER_ID}:${user.id}`)) {
        setLocallyDismissedFor(user.id);
        return;
      }
    } catch { /* The account preference still persists without browser storage. */ }
    if (activeBanner === null) claim(BANNER_ID);
  }, [user, isPending, plan, serverDismissed, locallyDismissedFor, activeBanner, claim]);

  if (!user || isPending || plan !== "free" || serverDismissed || locallyDismissedFor === user.id || activeBanner !== BANNER_ID) return null;

  function close() {
    if (!user) return;
    try { localStorage.setItem(`${BANNER_ID}:${user.id}`, "1"); } catch { /* Optional browser storage. */ }
    setLocallyDismissedFor(user.id);
    dismiss(BANNER_ID);
    void updatePreferences({ dismissBanner: BANNER_ID }).catch(() => {});
  }

  return <aside aria-label={t({ id: "pro.announcement.label", comment: "Accessible label for the dismissible gold Pro launch banner", message: "Job Seek Pro announcement" })}
    className={aboveBottomBar
      ? "fixed bottom-14 left-0 right-0 z-50 border-t border-pro-gold-border bg-pro-gold-bg backdrop-blur-sm md:static md:z-auto md:border-b md:border-t-0"
      : "border-b border-pro-gold-border bg-pro-gold-bg backdrop-blur-sm"}>
    <div className="mx-auto flex max-w-[1200px] flex-col gap-2 px-4 py-2 text-sm text-pro-gold sm:flex-row sm:items-center sm:gap-3">
      <div className="flex flex-1 items-start gap-2"><Crown size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
        <p><Trans id="pro.announcement.message" comment="Pro announcement; eligibility and billing terms appear on the linked offer page">Pro is here. Use your own criteria to narrow your watchlists. A 7-day free trial is available for first-time subscribers.</Trans></p>
      </div>
      <div className="flex shrink-0 items-center gap-2 self-end sm:self-auto">
        <NavLink href={lp("/settings#product-news")} prefetch={false} className="rounded-full px-2.5 py-1 font-medium transition-colors hover:bg-pro-gold-border"><Trans id="pro.announcement.updates" comment="Opens the optional marketing checkbox; clicking this link does not opt in">Product news</Trans></NavLink>
        <NavLink href={lp("/narrowed")} prefetch={false} className="rounded-full bg-pro-gold-border px-2.5 py-1 font-medium transition-colors hover:opacity-80"><Trans id="pro.announcement.cta" comment="Pro announcement link to explanation, pricing and trial terms">Explore Pro</Trans></NavLink>
        <button type="button" onClick={close} aria-label={t({ id: "pro.announcement.dismiss", comment: "Dismiss the Pro launch banner without changing marketing consent", message: "Dismiss Pro announcement" })} className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-full transition-colors hover:bg-pro-gold-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current"><X size={16} aria-hidden="true" /></button>
      </div>
    </div>
  </aside>;
}
