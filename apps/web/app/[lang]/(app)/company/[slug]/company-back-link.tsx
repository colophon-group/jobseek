"use client";

import { useSearchParams } from "next/navigation";
import { BackLink } from "@/components/BackLink";
import type { Locale } from "@/lib/i18n";

type Props = {
  locale: Locale;
  label: string;
};

/**
 * "Back to search" link that preserves the user's current URL filters.
 *
 * Lives in a client component (reading `useSearchParams()`) so the
 * surrounding company page can stay statically prerendered. Wrapped
 * in `<Suspense>` by the caller; until hydration the fallback shows
 * a plain `/explore` link without filter params.
 */
export function CompanyBackLink({ locale, label }: Props) {
  const sp = useSearchParams();
  const qs = sp.toString();
  const href = `/${locale}/explore${qs ? `?${qs}` : ""}`;
  return <BackLink href={href}>{label}</BackLink>;
}
