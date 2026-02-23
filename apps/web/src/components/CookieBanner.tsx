"use client";

import { useState, useEffect } from "react";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import Link from "next/link";
import { Info } from "lucide-react";

const STORAGE_KEY = "cookie-consent";

export function CookieBanner() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!localStorage.getItem(STORAGE_KEY)) {
      setVisible(true);
    }
  }, []);

  if (!visible) return null;

  function dismiss() {
    localStorage.setItem(STORAGE_KEY, "1");
    setVisible(false);
  }

  return (
    <div className="border-b border-info-border bg-info-bg">
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
            className="rounded-full bg-info-border px-2.5 py-1 font-medium transition-colors hover:opacity-80"
          >
            {t({ id: "common.cookies.ok", comment: "Cookie banner accept button", message: "Ok" })}
          </button>
        </div>
      </div>
    </div>
  );
}
