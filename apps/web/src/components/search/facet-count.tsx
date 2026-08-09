"use client";

import { useLingui } from "@lingui/react";

const formatters = new Map<string, Intl.NumberFormat>();

export function formatFacetCount(count: number, locale: string): string {
  let formatter = formatters.get(locale);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale);
    formatters.set(locale, formatter);
  }
  return formatter.format(count);
}

interface FacetCountProps {
  count: number;
  className?: string;
}

/** Locale-aware count shared by every filter-facet rendering shape. */
export function FacetCount({ count, className }: FacetCountProps) {
  const { i18n } = useLingui();
  return (
    <span className={className}>
      ({formatFacetCount(count, i18n.locale)})
    </span>
  );
}
