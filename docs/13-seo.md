# SEO

Covers the on-page SEO surface (head metadata, JSON-LD, similar-companies strip, watchlist stats row).

> **IndexNow retired (2026-05, #2843).** The crawler-side notifier was
> retired in #2821 when companies left the index, and the residual
> web-app surface (`apps/web/src/lib/indexnow.ts`, the
> `/indexnow-key.txt` route, the `indexnow_submission` Postgres table,
> the watchlist `notifyIndexNow` calls) was retired in #2843. Sitemap
> discovery is now the sole non-Google signal — Bing / Yandex / Seznam
> / Naver / Yep recrawl on a 1–7 day cadence at the qualifying-watchlist
> + blog surface size, and the IndexNow signal/noise ratio at this
> scale wasn't worth the maintenance footprint. Reviving the notifier
> for a per-posting-URL surface (if we ever expose `/job/{id}` routes)
> is a separate design conversation.

## On-page SEO

### Company page — server-rendered head

`apps/web/app/[lang]/(app)/company/[slug]/page.tsx` fetches `getCompanyBySlug` on the server and renders `<CompanyHead>` (server component) before the client `<CompanyContent>` wrapper. The head includes, in the initial SSR payload:

- `<h1>` with the company name (optionally wrapped in a `target="_blank" rel="noopener noreferrer"` anchor to the company's own website).
- Localized description paragraph: `getCompanyBySlug` reads from the Typesense `company` collection, picking `description_{locale}` and falling back to `description` (English). On Typesense error or 0 hits, the SQL fallback path runs the same `COALESCE(cd.description, c.description)` join against Supabase `company_description` it always did.
- Meta row: industry / employee-count range / founding year, all resolved to localized strings via `getI18n()` server-side.
- Back-navigation `<BackLink>` to `/{locale}/explore` preserving the current query string.
- `Organization` + `BreadcrumbList` JSON-LD blocks, via `<JsonLd>` in `apps/web/src/lib/seo.tsx`. `safeHttpUrl()` guards `logo` and `sameAs` so only http/https URLs reach the payload.

The **posting list stays client-rendered** deliberately. Postings are ephemeral (2–4 week shelf life) and duplicate of the origin career page's text — Google would canonicalize away to the source, and expired postings become dead indexed URLs.

After #2821, `/{locale}/company/{slug}` is `noindex,follow` and excluded from the sitemap. The page is still the canonical in-app entity surface and the JSON-LD blocks above remain useful for AI retrievers (Knowledge-Graph crawlers consume schema.org references regardless of robots), but it no longer competes for organic traffic.

### Similar companies strip

`apps/web/src/components/company/similar-companies-strip.tsx` renders a horizontal strip of same-industry peers between the info row and the stats row. Implementation notes:

- SSR-initial: `getSimilarCompanies` is called in `page.tsx` and its Promise is passed unawaited to `<SimilarSection>` wrapped in `<Suspense fallback={null}>`. If Typesense is slow, the head + postings column stream independently — the strip patches in when the Promise resolves.
- Client-side: the strip subscribes to `useSearchParams()` and refetches on URL change. When filters are active, counts per card reflect the filtered `job_posting` facet — they show "12 open positions" meaning "12 matching your current search," not "12 total."
- Pagination: reuses `useInfiniteScroll` + `InfiniteScrollSentinel` (horizontal orientation) + `ScrollFade` (horizontal orientation) to lazy-load batches as the user scrolls right.
- Anonymous cap: reuses `ANON_MAX_COMPANIES` (15). At the cap, a geometry-matched sign-in CTA card replaces the last pagination slot.
- Card links carry the current URL search params so filters survive lateral navigation between companies.

### Watchlist view — shared stats row

`apps/web/src/components/search/language-stats-row.tsx` renders "Showing jobs in {lang} · change ... N active · M in the last year" with a responsive split (md+ inline, sm drops stats to a dedicated row with active-left / year-right). The component is mounted inside `WatchlistJobList`'s `listColumn` so the row stays aligned with the postings list when the job-detail panel opens on the right.

`getWatchlistPostingYearCount` in `apps/web/src/lib/actions/watchlists.ts` runs in `Promise.all` with `getWatchlistPostings` inside `fetchWatchlistPageData`: one extra `job_posting` count query with `first_seen_at >= now() - 1 year` replacing `is_active:true`, same filters otherwise.

### Sitemap + hreflang

The single `/sitemap.xml` route (sharding retired in #2848) emits one `<urlset>` covering the static pages, `/explore`, qualifying watchlists (#2823), and blog posts (#2828). Each entry carries an `xhtml:link rel="alternate"` map for every locale, with `x-default` anchored at `/en` (#2825). Page-level `<head>` hreflang in `apps/web/src/lib/seo.tsx::buildAlternates` matches the sitemap and accepts an optional `availableLocales` parameter so partially-translated routes (per-post blog locales, #2849) only advertise locales with rendered content.

### Not covered

- **Per-posting URLs.** We don't expose `/job/{id}` routes. Google's Indexing API is restricted to `JobPosting` / `BroadcastEvent` schema; eligibility would require a strategic decision to expose posting detail pages, which we've ruled against.
- **Google coverage.** Google discovery relies on `sitemap.xml` plus the on-page work above; the historical ~2k/16k coverage gap was an authority/backlink problem that the company-page noindex resolved structurally — the indexable surface is now ~hundreds of URLs, not ~17K.
