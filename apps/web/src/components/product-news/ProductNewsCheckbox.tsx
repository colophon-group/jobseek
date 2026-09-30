"use client";

import { Trans } from "@lingui/react/macro";

export function ProductNewsCheckbox({ checked, disabled, onChange }: {
  checked: boolean; disabled?: boolean; onChange: (checked: boolean) => void;
}) {
  return <label className="flex cursor-pointer items-start gap-3 text-sm leading-relaxed has-disabled:cursor-wait has-disabled:opacity-60">
    <input type="checkbox" name="productNews" checked={checked} disabled={disabled}
      onChange={event => onChange(event.target.checked)}
      className="mt-1 h-4 w-4 shrink-0 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background" />
    <span><Trans id="productNews.consent.label" comment="Optional unchecked marketing opt-in, separate from job alerts; keep wording synchronized with product-news/policy.ts and version changes">Email me occasional Job Seek product updates and offers. Unsubscribe anytime.</Trans></span>
  </label>;
}
