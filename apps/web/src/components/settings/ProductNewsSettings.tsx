"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Trans, useLingui } from "@lingui/react/macro";
import { Mail } from "lucide-react";
import { ProductNewsCheckbox } from "@/components/product-news/ProductNewsCheckbox";
import { setProductNewsSettings } from "@/lib/actions/product-news";
import { PRODUCT_NEWS_CONSENT_VERSION } from "@/lib/product-news/policy";

export function ProductNewsSettings({ enabled, verified }: { enabled: boolean; verified: boolean }) {
  const router = useRouter();
  const { i18n } = useLingui();
  const [savedValue, setSavedValue] = useState(enabled);
  const [previousProp, setPreviousProp] = useState(enabled);
  const [busy, startTransition] = useTransition();
  const inFlight = useRef(false);
  const [status, setStatus] = useState<"saved" | "error" | null>(null);
  if (previousProp !== enabled) {
    setPreviousProp(enabled);
    setSavedValue(enabled);
  }

  function change(value: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    setStatus(null);
    startTransition(async () => {
      try {
        const result = await setProductNewsSettings(value, i18n.locale, PRODUCT_NEWS_CONSENT_VERSION);
        if ("error" in result) throw new Error(result.error);
        setSavedValue(result.enabled);
        setStatus("saved");
        router.refresh();
      } catch { setStatus("error"); }
      finally { inFlight.current = false; }
    });
  }

  return <section id="product-news" aria-labelledby="product-news-heading" className="scroll-mt-24">
    <h2 id="product-news-heading" className="flex items-center gap-2 text-lg font-semibold"><Mail size={19} aria-hidden="true" className="shrink-0 text-muted" /><Trans id="productNews.settings.title" comment="Marketing email settings heading">Product news</Trans></h2>
    <p className="mt-2 text-sm text-muted"><Trans id="productNews.settings.description" comment="Marketing consent is independent of requested job alerts and account emails">Optional emails about new features and Pro offers. This preference is separate from your weekly job emails and account emails.</Trans></p>
    <div className="mt-4" aria-busy={busy}><ProductNewsCheckbox checked={savedValue} disabled={busy} onChange={change} /></div>
    {!verified && <p className="mt-3 text-sm text-muted"><Trans id="productNews.settings.verify" comment="Unverified accounts can choose consent but cannot receive marketing">Verify your email address before product news can be delivered.</Trans></p>}
    <p role="status" aria-live="polite" className={status ? "mt-3 text-sm" : "sr-only"}>
      {status === "saved" ? <Trans id="productNews.settings.saved" comment="Confirmation after marketing preference is persisted">Product news preference saved.</Trans> : status === "error" ? <Trans id="productNews.settings.error" comment="Marketing preference failed; checkbox retains the saved value">Could not save your preference. Please try again.</Trans> : null}
    </p>
  </section>;
}
