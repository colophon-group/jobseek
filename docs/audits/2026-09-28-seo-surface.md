# SEO surface audit — 28 September 2026

## Remediation

The follow-up implementation adopts the decision to remove Explore from search indexing. It adds `noindex,follow` and reduces the sitemap to 28 URLs; Explore's client rendering is therefore no longer an SEO requirement.

The changes also move automatic IndexNow submission after verified production promotion, make missing notification credentials fail, add account/auth noindex while allowing crawlers to read it, correct static/article dates and per-translation freshness, guard missing/untranslated blog URLs before streaming, remove repeated homepage branding, align article social images, add Article image/verified author identity, and update discovery/runbook documentation. A PR date gate prevents stale modification dates on edited existing articles.

Local verification: production build, TypeScript, changed-file ESLint, 2,830 web tests, 20 production-build checks, repository workflow tests, all-locale production smoke checks, actionlint, and zizmor passed. The original observations below remain an audit baseline, not a claim about the post-remediation deployment.

## Assessment

Job Seek has a sound foundation for a **small, multilingual product-and-editorial site**. It currently advertises **32 URLs: eight distinct pages in four languages**. Every sitemap URL returned HTTP 200 with a self-canonical URL, matching HTML/sitemap language alternates, a matching `html lang`, a description, and one H1 in the raw response.

It does **not** currently expose its company and job inventory as an indexable landing-page network. Company pages are intentionally `noindex,follow`; watchlists are private or shared product resources; individual job detail routes do not exist. That is a product/acquisition choice, not a missing-sitemap bug.

The most consequential technical finding is that **Explore still delivers a loading shell to clients that do not execute JavaScript**, despite embedding initial result data. The most concrete operational failure is that **the four latest blog IndexNow workflows failed**, with the two inspected runs timing out before submission. Content freshness and the limited number of distinct editorial pages constrain the surface more than additional schema markup would.

This audit does not establish actual indexing, rankings, traffic, or backlink quality. Those require Search Console, Bing Webmaster Tools, and acquisition data.

## Scope and evidence

- Isolated worktree: `/Users/Viktor/.codex/worktrees/seo-surface-audit/jobseek`.
- Branch: `codex/seo-surface-audit`.
- Base: freshly fetched `origin/main`, [a91adb088c9f2e5c6a0a2e29c56a03e7add220db](https://github.com/colophon-group/jobseek/commit/a91adb088c9f2e5c6a0a2e29c56a03e7add220db).
- Reviewed App Router routes, metadata, robots, sitemap, localization, structured data, blog content, redirects, IndexNow, and relevant tests.
- Probed all 32 live sitemap URLs, 21 additional URLs, and four repeat requests using a Googlebot user-agent string: **57 anonymous GET probes**, plus sitemap and image checks.
- Compared production Explore in Chromium with JavaScript disabled and enabled. No login, mutations, indexing submissions, or deployment changes.
- [Machine-readable evidence](./2026-09-28-seo-surface-evidence.json) records responses, metadata, schema types, and browser observations. Collection timestamps are in UTC. Production was observed independently; its exact deployed Git SHA was not established.
- Source references below are relative to this checkout. No application code was changed during the initial audit; remediation is recorded above.

## Current surface

| Surface | Indexing/discovery policy | Observed or source-verified state |
|---|---|---|
| `/{lang}` | Indexable; sitemap | Four translated homepages; product positioning, features, pricing, internal navigation |
| `/{lang}/about`, `/faq` | Indexable; sitemap | Eight translated pages; content present in raw HTML |
| `/{lang}/blog` | Indexable; sitemap | Four indexes linking all three posts |
| `/{lang}/blog/{slug}` | Indexable; sitemap only for available translations | Three posts, each translated into all four languages: 12 URLs |
| `/{lang}/explore` | Indexable; sitemap | Four URLs; metadata works, result content requires JavaScript |
| Explore filter URLs | Canonical to queryless Explore | `/en/explore?q=python` returns 200 and canonicalizes to `/en/explore`; not separate search landing pages |
| `/{lang}/company/{slug}` | `noindex,follow`; absent from sitemap | Stripe sample returns 200 and noindex; unknown company sample returns actual 404 |
| `/{lang}/watchlists/{UUID}` | `noindex,nofollow`; absent from sitemap | Owner/shared-link product surface; route metadata verified in source |
| Legacy `/{lang}/{user}/{watchlist}` | Anonymous 404; owner compatibility redirect | Anonymous sample returns 404 with noindex |
| Terms, privacy, license, how-we-index | `noindex,follow`; absent from sitemap | All four English samples match policy |
| Authentication and personal account routes | Robots.txt disallows crawling | Some lack noindex; see finding 3 |
| Individual jobs | No dedicated indexable URL or JobPosting schema | Job detail is a product UI; Google job rich-result eligibility is not currently implemented |
| AI/API discovery | Explicit AI robots group; llms.txt/OpenAPI | `/.well-known/llms.txt` returns 200; `/llms.txt` redirects there |

Sources: `apps/web/src/lib/sitemap.ts`, `src/content/config.ts`, `app/robots.ts`, `app/[lang]/(app)/company/[slug]/page.tsx`, `app/[lang]/(app)/watchlists/[watchlistId]/page.tsx`, and the legacy watchlist `route.ts`.

The sitemap contains 20 core-page URLs and 12 article URLs. All dates are in May 2026. There are no company, private-watchlist, or legal URLs in it.

## Prioritized findings

### 1. High — Explore's result content falls back to client rendering

**Observation.** All four raw Explore responses contain the heading/navigation/loading state but no company anchors. The English response has approximately 17 non-script words, including its title. It contains `BAILOUT_TO_CLIENT_SIDE_RENDERING` markers and initial company data inside the React payload, rather than rendered result markup.

**Verification.** Chromium with JavaScript disabled stays on “Loading results.” With JavaScript enabled, the same URL shows job listings and ten company links. The Googlebot-string probe also receives the loading shell. This is a rendering dependency, not proof that Google's renderer fails or that Explore is absent from its index.

**Implication.** The page relies on hydration for its main content and outgoing company links. Embedding data in scripts does not provide equivalent ordinary HTML content to clients that do not run the application. Google can render JavaScript, but rendering remains a separate stage. [Google JavaScript SEO guidance](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics).

**Recommendation.** Keep an anonymous, useful result sample and explanatory copy in server-rendered HTML. Isolate request-dependent controls inside smaller Suspense boundaries. Preserve the existing filter canonical policy. Source entry points: `app/[lang]/(app)/explore/page.tsx:73`, `explore-content.tsx:35`, `search-page.tsx`, and descendants. The exact component causing the production bailout was not isolated; do not assume that every “use client” component prevents SSR.

**Acceptance.** A production-build HTTP/browser check with JavaScript disabled finds result text and company anchors on every queryless locale route; filtered and personalized views still initialize correctly. If Explore is intentionally only a JavaScript application, explicitly reconsider its role as an indexable acquisition page.

### 2. Medium — Blog IndexNow notifications are failing before submission

**Observation.** The latest five workflow runs were four failures followed by one success:

| Run date | Result |
|---|---|
| [2026-09-27](https://github.com/colophon-group/jobseek/actions/runs/36330596566) | Failed; deployment wait timed out |
| [2026-09-11](https://github.com/colophon-group/jobseek/actions/runs/34619501433) | Failed; deployment wait timed out |
| [2026-09-10](https://github.com/colophon-group/jobseek/actions/runs/34515840209) | Failed; cause not inspected |
| [2026-07-22](https://github.com/colophon-group/jobseek/actions/runs/29940659241) | Failed; cause not inspected |
| [2026-05-07](https://github.com/colophon-group/jobseek/actions/runs/25519231065) | Success |

**Cause evidence.** `.github/workflows/notify-blog-indexnow.yml` requires a GitHub deployment for the exact SHA, environment `Production`, and creator `vercel[bot]`. The deployment records returned for the September 27 SHA contain no `vercel[bot]` creator; they contain human/GitHub Actions creators and both `Production` and `production` environments. The predicate therefore finds no acceptable record. The September 27 and September 11 logs confirm ten-minute timeouts.

**Implication.** Those runs never reach the URL-submission step. This does not prevent sitemap discovery and is not evidence of a Google indexing outage. The public IndexNow proof endpoint itself returned 200.

**Recommendation.** Tie the notification to the actual production deploy completion mechanism, or validate a reliable deployment identity/content version. Keep submission failure reporting, and make a missing production key fail explicitly instead of returning a successful no-op.

**Acceptance.** A content deployment reaches the submit step only after the matching content is live and records a 200/202 acknowledgement. No submissions were triggered during this audit.

### 3. Medium — Robots.txt is being used as the only exclusion for several account pages

**Observation.** `/en/sign-in`, `/en/sign-up`, `/en/forgot-password`, `/en/my-jobs`, and `/en/settings` return 200 without a robots noindex tag or X-Robots-Tag. Robots.txt disallows these paths. By comparison, `/en/watchlists` explicitly emits `noindex,nofollow`.

**Implication.** Crawl blocking alone does not guarantee exclusion from search results; a crawler can discover a URL through links while being unable to read page directives. This is an indexing-policy issue, not evidence of exposed private data. [Google noindex requirements](https://developers.google.com/search/docs/crawling-indexing/block-indexing).

**Recommendation.** Add consistent noindex metadata to non-acquisition account/auth routes, scoped so Explore remains indexable. For public anonymous shells that must disappear from search, permit enough crawling to observe noindex; retain real access controls for private content.

**Acceptance.** Route-by-route tests cover noindex in the delivered response and confirm robots policy does not prevent the intended removal signal from being read. Sources: `src/content/config.ts:190`, `app/robots.ts`, `app/[lang]/(auth)/layout.tsx`, and personal route metadata.

### 4. Medium — Modification dates are stable but no longer truthful

**Observation.** All 20 core sitemap entries report May 1; all 12 article entries report May 7. Yet the homepage pricing/features changed September 27, Explore changed in September, and the indexing article's body changed September 27 without updating its May 7 `dateModified`.

**Evidence.** `src/content/config.ts:219`; blog frontmatter; commits `52e8c7b6a` and `012191b81`. The latter replaces a substantive paragraph in `how-we-index-job-postings.mdx`. Blog sitemap generation also uses the canonical English post's date for every translation, so a later translation-only update would be missed.

**Implication.** Recrawl freshness signals understate actual changes. This is not a reason to stamp every response with today's date. Google recommends accurate lastmod values tied to meaningful content changes. [Sitemap guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap).

**Recommendation.** Update dates with substantive content changes, use per-translation modification dates where appropriate, and add an authoring/review check. Derive blog-index freshness from its content where feasible.

**Acceptance.** An edited page or translation advances only the affected truthful date; an unchanged rebuild does not.

### 5. Low — Missing blog slugs return HTTP 200

**Observation.** `/en/blog/seo-audit-missing-post-928` returns 200, title “Job Seek,” and a noindex tag, for both normal and Googlebot-string probes. Unknown company, legacy watchlist, and generic-page samples return real 404s.

**Interpretation.** This matches Next.js's documented behavior when `notFound()` happens after streaming begins. The noindex signal mitigates indexing risk; it is not a valid article accidentally advertised in the sitemap. Installed framework reference: `node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/not-found.md`.

**Recommendation.** Resolve known-invalid blog slugs before streaming, using the repository's finite post inventory. Avoid a fix that breaks Cache Components. Source: `app/[lang]/(public)/blog/[slug]/page.tsx:25`, `:49`, and `:138`.

**Acceptance.** Unknown localized blog paths return 404 with an appropriate localized error title for GET and HEAD; valid articles still stream/cache normally.

### 6. Low — Metadata polish and an untested translation fallback

- **Repeated branding:** every homepage title ends in “— Job Seek | Job Seek.” The page title includes the brand and the root title template adds it again. Remove one copy. Sources: public `page.tsx:21` and locale `layout.tsx:28`.
- **Blog social-card mismatch:** articles emit a post-specific `og:image`, but inherit the generic site `twitter:image`. Both sampled image endpoints return 200. Override Twitter images per article if consistent article previews are intended.
- **Article enrichment:** Article JSON-LD includes headline, dates, author, publisher, language, and canonical page identity, but lacks an article image and author URL. Add suitable real assets/identity pages when available; these are enhancements, not proof of invalid schema. [Google Article guidance](https://developers.google.com/search/docs/appearance/structured-data/article).
- **Latent fallback inconsistency:** `getBlogPost(slug, locale)` falls back to English, while page metadata still self-canonicalizes to the requested locale and Article `inLanguage` uses that locale. Hreflang correctly omits untranslated variants, but that alone does not canonicalize the fallback. All current posts have all translations, so this is a source-level risk for the next partially translated post. Resolve the effective content locale and deliberately redirect or canonicalize English fallbacks. Test with an English-only fixture. Sources: `src/lib/blog.ts:233`, `src/lib/seo.tsx:22`, blog `page.tsx:48–62` and `:98`.

### 7. Low — The SEO runbook describes retired behavior

`docs/13-seo-and-indexnow.md` still describes public watchlists in the sitemap, active watchlist-side notifications, and a five-minute deploy sleep. Current code excludes watchlists, contains no `notifyIndexNow` calls in watchlist actions, and polls deployment records. Its historical “~2k/16k coverage gap is an authority/backlink problem” claim is not a current diagnosis and is unsupported by this audit.

Update the runbook around the present 32-URL surface and distinguish historical decisions from active operations. The AI discovery file could also link to the blog and its research article; it currently omits both. Treat that as discovery hygiene, not a promised ranking benefit.

## What is working

- Sitemap and page canonical/hreflang agree across all 32 URLs, including reciprocal `en/de/fr/it` and English `x-default`. No sitemap entries were redirected, noindexed, or missing in this sample. [Google localized-page guidance](https://developers.google.com/search/docs/specialty/international/localized-versions).
- Marketing and article bodies are present in raw HTML; all three posts have all four translations.
- Query URLs converge to the clean Explore canonical, avoiding accidental indexing of every filter permutation.
- Company/legal noindex is readable because those routes remain crawlable. Legacy anonymous watchlist paths return explicit 404s.
- Root redirects to `/en` for the tested no-preference request; locale URLs are directly accessible.
- JSON-LD parses successfully throughout the sampled HTML. The shared renderer escapes `<` in serialized data.
- Organization, WebSite, WebApplication, article/page/FAQ schema provide semantic descriptions. Presence alone does not imply rich-result eligibility.
- Existing focused checks pass: **7 test files, 55 tests** covering SEO helpers, sitemap, robots, IndexNow, and not-found metadata.

## Acquisition implications and next steps

There are only **eight distinct indexable page concepts**, including three articles published May 7. Two articles explain the product/process; the remote-engineering analysis is the main broader research/search asset. Translating these to 32 URLs improves language coverage but does not create 32 distinct topics.

The research article explicitly describes May data, so its historical numbers are not inherently wrong. A new dated analysis or substantive refresh would create a stronger current acquisition surface than silently relabeling the old snapshot. Keep the methodology, collection date, source counts, and useful path into the product.

Prioritize work in this order:

1. **Restore the intended existing surface:** Explore HTML rendering, the failed IndexNow gate, and consistent account-page exclusion.
2. **Improve maintenance:** truthful lastmod values, missing-blog status checks, metadata cleanup, updated runbook.
3. **Measure:** review Search Console and Bing indexing by locale and route family; separate deliberate company/watchlist removals from failures on the surviving 32 URLs. Inspect Explore's rendered HTML and selected canonical in the engine tools.
4. **Expand deliberately:** publish more original hiring analyses or a small set of useful, maintained occupation/location/company-watchlist guides. Require unique content and a clear user need before adding indexable routes. Do not mass-index empty filter combinations or reverse company noindex merely to increase URL counts.

A per-job acquisition strategy would require dedicated detail pages, complete visible descriptions, valid JobPosting data, and expired-job handling. It is a separate product decision; adding JobPosting markup to Explore lists is not the shortcut. [Google JobPosting requirements](https://developers.google.com/search/docs/appearance/structured-data/job-posting).

## Verification and limitations

Focused test command, from `apps/web`:

```sh
pnpm exec vitest run \
  src/lib/__tests__/seo.test.ts \
  src/lib/__tests__/sitemap.test.ts \
  src/lib/__tests__/indexnow.test.ts \
  src/lib/__tests__/indexnow-observability.test.ts \
  app/__tests__/robots.test.ts \
  app/__tests__/sitemap-xml-route.test.ts \
  'app/[lang]/__tests__/not-found-metadata.test.ts'
```

Dependencies were installed from the lockfile in the isolated checkout. The first test run could not resolve the unbuilt workspace MCP package; after `pnpm --filter @jseek/mcp-server build`, all 55 tests passed. This was setup, not an application fix.

To reproduce representative HTTP observations:

```sh
curl -sS -D - https://jseek.co/sitemap.xml
curl -sS -A Googlebot https://jseek.co/en/explore
curl -sS -D - https://jseek.co/en/blog/seo-audit-missing-post-928
gh run list --workflow notify-blog-indexnow.yml --branch main --limit 5
```

No full local production build, Lighthouse/CrUX measurement, authenticated flow audit, full company crawl, actual search-engine URL inspection, or backlink analysis was performed. Response size and a successful Chromium render are not Core Web Vitals measurements. A simulated Googlebot user-agent is not a request from Google's infrastructure. Exact ranking effects and traffic opportunity remain unquantified.

