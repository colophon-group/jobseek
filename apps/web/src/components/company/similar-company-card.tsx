"use client";

import Link from "next/link";
import Image from "next/image";
import { Building2 } from "lucide-react";
import { Plural } from "@lingui/react/macro";
import type { SimilarCompany } from "@/lib/actions/company";
import type { Locale } from "@/lib/i18n";

type Props = {
  company: SimilarCompany;
  locale: Locale;
  /** Raw URL query string (without "?") forwarded onto the card link so
   *  filters persist when the user clicks through to another company. */
  preserveParams?: string;
};

export function SimilarCompanyCard({ company, locale, preserveParams }: Props) {
  // Defensive coercion: if a non-numeric snuck in (string from a
  // Typesense edge case, undefined from a miss), ICU renders "NaN".
  const count = Number.isFinite(company.activeJobCount)
    ? (company.activeJobCount as number)
    : Number(company.activeJobCount) || 0;
  const href = preserveParams
    ? `/${locale}/company/${company.slug}?${preserveParams}`
    : `/${locale}/company/${company.slug}`;

  return (
    <li className="shrink-0 snap-start">
      <Link
        href={href}
        prefetch={false}
        className="flex h-full w-36 flex-col items-start gap-2 overflow-hidden rounded-md border border-border-soft bg-surface p-3 text-left transition-colors hover:border-primary/30 hover:bg-border-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        {company.icon ? (
          <Image
            src={company.icon}
            alt=""
            width={28}
            height={28}
            sizes="28px"
            className="size-7 shrink-0 rounded"
          />
        ) : (
          <div
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded bg-border-soft text-muted"
          >
            <Building2 size={16} />
          </div>
        )}
        <span className="line-clamp-2 w-full min-w-0 text-sm font-medium text-foreground [overflow-wrap:anywhere]">
          {company.name}
        </span>
        {/* mt-auto pushes the count to the bottom of the card so cards
            with short vs long names still have a stable baseline for the
            count line. */}
        <span className="mt-auto text-xs text-muted">
          <Plural
            id="company.similar.positionCount"
            comment="Active posting count on each similar-company card"
            value={count}
            one="# open position"
            other="# open positions"
          />
        </span>
      </Link>
    </li>
  );
}
