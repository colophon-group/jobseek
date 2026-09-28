"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { LogIn } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { ScrollFade } from "@/components/ui/scroll-fade";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { useSession } from "@/components/providers/SessionProvider";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { getSimilarCompanies, type SimilarCompany } from "@/lib/actions/company";
import { tryGetSimilarCompaniesDirect } from "@/lib/search/search-runner";
import type { Locale } from "@/lib/i18n";
import { SimilarCompanyCard } from "./similar-company-card";

type Props = {
  companyId: string;
  industryId: number | null;
  initialCompanies: SimilarCompany[];
  initialHasMore: boolean;
  /** True when anonymous-user pagination cap is already reached on page 0. */
  initialTruncated?: boolean;
  locale: Locale;
};

const PAGE_SIZE = 10;

export function SimilarCompaniesStrip({
  companyId,
  industryId,
  initialCompanies,
  initialHasMore,
  initialTruncated = false,
  locale,
}: Props) {
  const { t } = useLingui();
  const { isLoggedIn } = useSession();
  const [companies, setCompanies] = useState<SimilarCompany[]>(initialCompanies);
  const [hasMore, setHasMore] = useState(initialHasMore);
  const [truncated, setTruncated] = useState(initialTruncated);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Related companies always show global open-position totals. Reading the
  // company collection directly avoids a server action and filtered facets.
  useEffect(() => {
    const currentIndustryId = industryId;
    if (currentIndustryId == null) return;
    let cancelled = false;
    (async () => {
      const next = await tryGetSimilarCompaniesDirect({
        companyId,
        industryId: currentIndustryId,
        limit: PAGE_SIZE,
      });
      if (!next || cancelled) return;
      setCompanies(next.companies);
      setHasMore(next.hasMore);
      setTruncated(next.truncated ?? false);
      scrollRef.current?.scrollTo({ left: 0 });
    })();
    return () => {
      cancelled = true;
    };
  }, [companyId, industryId]);

  const loadMore = useCallback(async () => {
    if (industryId == null) return;
    const next = process.env.NEXT_PUBLIC_TYPESENSE_DIRECT === "1"
      ? await tryGetSimilarCompaniesDirect({
          companyId,
          industryId,
          offset: companies.length,
          limit: PAGE_SIZE,
        }, isLoggedIn)
      : await getSimilarCompanies(companyId, industryId, {
          offset: companies.length,
          limit: PAGE_SIZE,
        });
    // Let the scroll hook wait for another user scroll before retrying.
    if (!next) throw new Error("Related companies unavailable");
    setCompanies((prev) => {
      const seen = new Set(prev.map((c) => c.id));
      return [...prev, ...next.companies.filter((c) => !seen.has(c.id))];
    });
    setHasMore(next.hasMore);
    setTruncated(next.truncated ?? false);
  }, [companyId, industryId, companies.length, isLoggedIn]);

  const { sentinelRef, isLoading } = useInfiniteScroll({
    hasMore,
    load: loadMore,
    root: scrollRef,
    rootMargin: "0px 200px 0px 0px",
  });

  if (companies.length === 0) return null;

  const headingId = "similar-companies-heading";
  const heading = t({
    id: "company.similar.heading",
    comment: "Heading above the strip of same-industry companies on the company page",
    message: "Similar companies",
  });

  return (
    <>
      <hr className="border-divider" />
      <nav aria-labelledby={headingId} className="space-y-2">
        <h2
          id={headingId}
          className="text-xs font-medium uppercase tracking-wide text-muted"
        >
          {heading}
        </h2>
        <ScrollFade direction="horizontal" scrollRef={scrollRef} deps={[companies.length, truncated]}>
          <ul role="list" className="flex snap-x snap-mandatory gap-2 pb-2">
            {companies.map((company) => (
              <SimilarCompanyCard
                key={company.id}
                company={company}
                locale={locale}
              />
            ))}
            {hasMore && (
              <InfiniteScrollSentinel
                sentinelRef={sentinelRef}
                isLoading={isLoading}
                size="sm"
                orientation="horizontal"
              />
            )}
            {!hasMore && truncated && <SignInPromptCard />}
          </ul>
        </ScrollFade>
      </nav>
    </>
  );
}

/**
 * Horizontal-strip sign-in prompt. Matches the geometry of
 * `SimilarCompanyCard` (w-36, h-full, p-3, rounded) so it flows
 * naturally as the final scrollable item. Visually distinct via a
 * dashed primary-coloured border so it reads as a CTA rather than
 * another peer company.
 *
 * Mirrors the guard in `TruncationPrompt`: skip rendering while the
 * session is still resolving so we don't flash the prompt at a
 * logged-in user during hydration.
 */
function SignInPromptCard() {
  const { isPending } = useSession();
  const params = useParams();
  const lang = (params.lang as string) ?? "en";
  if (isPending) return null;
  return (
    <li className="shrink-0 snap-start">
      <Link
        href={`/${lang}/sign-in`}
        prefetch={false}
        className="flex h-full w-36 flex-col items-start justify-between gap-2 rounded-md border border-dashed border-primary/60 bg-primary/5 p-3 text-left text-primary transition-colors hover:border-primary hover:bg-primary/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        <LogIn size={20} aria-hidden="true" />
        <div className="space-y-1">
          <p className="text-sm font-medium leading-tight">
            <Trans
              id="company.similar.signIn.title"
              comment="Title on the sign-in CTA card shown to anonymous users at the end of the similar-companies strip"
            >
              Sign in for more
            </Trans>
          </p>
          <p className="text-xs text-primary/80">
            <Trans
              id="company.similar.signIn.subtitle"
              comment="Subtitle on the sign-in CTA card shown to anonymous users at the end of the similar-companies strip"
            >
              Browse every peer company
            </Trans>
          </p>
        </div>
      </Link>
    </li>
  );
}
