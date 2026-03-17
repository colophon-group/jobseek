"use client";

import { useState, useEffect } from "react";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { AlertTriangle } from "lucide-react";
import { useLocalePath } from "@/lib/useLocalePath";
import { useFollowedCompanies } from "@/components/FollowedCompaniesProvider";
import Link from "next/link";

const STORAGE_KEY = "upgrade-banner-dismissed";

export function UpgradeBanner({ aboveBottomBar }: { aboveBottomBar?: boolean }) {
  const { t } = useLingui();
  const lp = useLocalePath();
  const { limitReached, followCount, followMax } = useFollowedCompanies();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!limitReached) return;
    if (!localStorage.getItem(STORAGE_KEY)) {
      setVisible(true);
    }
  }, [limitReached]);

  if (!visible) return null;

  function dismiss() {
    localStorage.setItem(STORAGE_KEY, "1");
    setVisible(false);
  }

  return (
    <div className={aboveBottomBar
      ? "fixed bottom-[52px] left-0 right-0 z-50 border-t border-warning-border bg-warning-bg backdrop-blur-sm md:sticky md:top-12 md:bottom-auto md:z-40 md:border-b md:border-t-0"
      : "border-b border-warning-border bg-warning-bg backdrop-blur-sm"
    }>
      <div className="mx-auto flex max-w-[1200px] flex-col gap-2 px-4 py-2 text-sm text-warning sm:flex-row sm:items-center sm:gap-3">
        <div className="flex flex-1 items-start gap-2">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <p>
            <Trans
              id="plans.followLimit.banner"
              comment="Banner shown when user hits free plan follow limit"
            >
              You&apos;re following {followCount} of {followMax} companies on the free plan.
            </Trans>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 self-end sm:self-auto">
          <button
            onClick={dismiss}
            className="rounded-full px-2.5 py-1 font-medium transition-colors hover:bg-warning-border cursor-pointer"
          >
            {t({ id: "plans.followLimit.ok", comment: "Dismiss upgrade banner button", message: "Ok" })}
          </button>
          <Link
            href={lp("/app/settings/billing")}
            prefetch={false}
            onClick={dismiss}
            className="rounded-full bg-warning-border px-2.5 py-1 font-medium transition-colors hover:opacity-80"
          >
            <Trans id="plans.followLimit.upgrade" comment="Link to upgrade to Pro plan">
              Upgrade to Pro
            </Trans>
          </Link>
        </div>
      </div>
    </div>
  );
}
