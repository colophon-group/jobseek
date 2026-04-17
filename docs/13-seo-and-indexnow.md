# SEO and IndexNow

Covers the company-page SSR surface (head metadata, JSON-LD, similar-companies strip, watchlist stats row) and the IndexNow notifier (Bing / Yandex / Seznam / Naver / Microsoft Yep — Google does **not** participate in IndexNow).

## On-page SEO

### Company page — server-rendered head

`apps/web/app/[lang]/(app)/company/[slug]/page.tsx` fetches `getCompanyBySlug` on the server and renders `<CompanyHead>` (server component) before the client `<CompanyContent>` wrapper. The head includes, in the initial SSR payload:

- `<h1>` with the company name (optionally wrapped in a `target="_blank" rel="noopener noreferrer"` anchor to the company's own website).
- Localized description paragraph from `company_description` with `COALESCE(cd.description, c.description)` fallback to the English row.
- Meta row: industry / employee-count range / founding year, all resolved to localized strings via `getI18n()` server-side.
- Back-navigation `<BackLink>` to `/{locale}/explore` preserving the current query string.
- `Organization` + `BreadcrumbList` JSON-LD blocks, via `<JsonLd>` in `apps/web/src/lib/seo.tsx`. `safeHttpUrl()` guards `logo` and `sameAs` so only http/https URLs reach the payload.

The **posting list stays client-rendered** deliberately. Postings are ephemeral (2–4 week shelf life) and duplicate of the origin career page's text — Google would canonicalize away to the source, and expired postings become dead indexed URLs.

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

### Not covered

- **Per-posting URLs.** We don't expose `/job/{id}` routes. Google's Indexing API is restricted to `JobPosting` / `BroadcastEvent` schema; eligibility would require a strategic decision to expose posting detail pages, which we've ruled against.
- **Google coverage.** IndexNow doesn't reach Google. Google discovery relies on `sitemap.xml` plus the on-page work above; the ~2k/16k coverage gap is an authority/backlink problem, not a discovery problem.

## IndexNow

Split by where the change event lives:

- **Companies** — change via CSV sync on the crawler side. A notifier on Hetzner diffs content hashes and submits changed URLs on a timer.
- **Watchlists** — change via user actions in the web app. Server actions call a fire-and-forget notifier from inside `after()` at mutation commit time.

### Endpoint

All submissions go to `https://api.indexnow.org/indexnow`. A single POST propagates to every participating engine — Bing, Yandex, Seznam, Naver, Microsoft Yep, and Bing-backed DuckDuckGo. Per-engine endpoints (e.g. `www.bing.com/indexnow`) are equivalent aliases; we use the generic one to keep the payload engine-neutral.

### Key management

Four env vars, configured symmetrically on the web and crawler sides:

| Variable | Default | Where |
|----------|---------|-------|
| `INDEXNOW_KEY` | — (required to enable) | Vercel (Production + Preview); GitHub Actions secret; `apps/*/.env.local` locally |
| `INDEXNOW_SITE_URL` | — | Crawler only (`https://jseek.co`; web derives from `siteConfig.url`) |
| `INDEXNOW_KEY_URL` | — | Crawler only (`https://jseek.co/indexnow-key.txt`) |
| `INDEXNOW_INTERVAL` | `3600` | Crawler only; seconds between notifier loops |

The key file is served by `apps/web/app/indexnow-key.txt/route.ts` with `dynamic = "force-dynamic"` + `Cache-Control: no-store`, so rotating the key takes effect on the next request (no rebuild required).

`INDEXNOW_HOST` on the crawler is derived from `INDEXNOW_SITE_URL` by a pydantic `@model_validator(mode="after")` — setting `indexnow_site_url=https://jseek.co` yields `indexnow_host=jseek.co` automatically, and trailing slashes are stripped to prevent double-slash URLs downstream.

### Crawler-side (companies): content-hash diff

`apps/crawler/src/indexnow.py::notify_indexnow` — runs on the `indexnow` container every `INDEXNOW_INTERVAL` seconds. Single Postgres connection across the whole cycle to avoid TOCTOU races with `crawler sync`:

1. Fetch every company + its stable fields (`name, website, logo, icon, industry, employee_count_range, founded_year`).
2. Fetch all `company_description` rows for supported locales.
3. Fetch the prior `{url: content_hash}` map from `indexnow_submission`.
4. For each (company × locale) pair:
   - `hash = "v2:" + sha256(stable_fields || description_for_locale || "")`
   - If `prior.get(url) != hash`, enqueue the URL for submission.
5. Batch ≤ 10 000 URLs per request (protocol cap), POST to the endpoint.
6. On 200/202, upsert `(url, hash, now())` into `indexnow_submission`. On 4xx (ERROR log), 5xx / network error (WARNING log), leave the row untouched — the next tick retries with the same payload.

**Hash scheme details:**

- Per-locale: a German description rewrite re-notifies `/de/company/{slug}` and leaves the other three URLs' stored hashes intact. See `compute_company_locale_hash` in `indexnow.py` and the `TestPerLocaleIsolation` class in `tests/test_indexnow.py`.
- Versioned prefix (`_HASH_VERSION = "v2"`) so scheme changes force a one-off full resubmit on the next tick rather than silently looking current. Prefix bumps happen in the module, no migration required.
- Stable fields are hashed once per company; the locale description is appended. Separator is `\x1f` (unit-separator) to avoid collisions when values contain common punctuation.

**Why not `updated_at`:** `company.updated_at` is re-stamped by `crawler sync` on every row every run, regardless of whether any column actually changed. It's not a change signal. The hash is the change signal.

**What's excluded:**

- Ephemeral posting list (client-rendered, not in the SEO surface).
- Company taxonomy changes that don't affect bot-visible HTML — tracked via the stable-field tuple.
- Watchlist URLs (handled web-side; see below).

### Web-side (watchlists): event-driven via `after()`

`apps/web/src/lib/indexnow.ts::notifyIndexNow` — called from server actions in `apps/web/src/lib/actions/watchlists.ts` at four points:

| Action | Trigger | URLs notified |
|--------|---------|---------------|
| `createWatchlist` | `isPublic && !trivial` | `/{userSlug}/{slug}` |
| `updateWatchlist` (keep-indexed path) | `shouldIndex` | new slug; **and** old slug if title rename changed slug |
| `updateWatchlist` (unindex path) | `else if (wasPublic)` | old slug — triggers re-crawl → 404 discovery |
| `copyWatchlist` | `!trivial` | new copy's slug |
| `deleteWatchlist` | `wl.isPublic` | old slug |

Implementation:

- No diff table. The mutation is the event — we know something changed because we just committed to Postgres.
- Uses `after()` from `next/server` so the POST runs after the response is flushed but before the serverless function terminates on Vercel (avoids the classic "fire-and-forget promise killed at function exit" failure mode).
- `encodeURIComponent` per path segment guards against user slugs with spaces / unicode / `%`-sequences.
- Gated on `owner.username` being truthy, matching the sitemap's `WHERE u.username IS NOT NULL` filter — we don't notify URLs the sitemap doesn't expose.
- No-op if `process.env.INDEXNOW_KEY` is unset (safe locally + on preview deploys without the secret).
- `AbortSignal.timeout(10000)` so a hung endpoint doesn't pin the function.

### URL resolution alignment

`getWatchlistByUserAndSlug` in `watchlists.ts` matches **either** `u.username` **or** `u.display_username` (preferring `username` via `ORDER BY (u.username = $1)::int DESC`). The sitemap emits URLs with `COALESCE(display_username, username)`, so without this fix a user with a distinct `display_username` would advertise URLs in `sitemap.xml` that the detail page couldn't resolve.

## Deployment

### Web (Vercel)

1. Set `INDEXNOW_KEY` in Vercel env → Production + Preview scopes.
2. Redeploy (env vars are baked per-deployment; existing deployments keep their snapshot).
3. Verify: `curl https://jseek.co/indexnow-key.txt` returns the key with `HTTP/2 200`.

### Crawler (Hetzner)

Fully CI-driven via `.github/workflows/deploy-crawler-browser.yml`:

1. Add GitHub Actions secrets: `INDEXNOW_KEY`, `INDEXNOW_SITE_URL`, `INDEXNOW_KEY_URL`, `INDEXNOW_INTERVAL`.
2. Push to `main` with any `apps/crawler/**` or workflow change.
3. CI builds + pushes the `ghcr.io/{owner}/jobseek-crawler:latest` image, `scp`'s `deploy.sh` + `docker-compose.yml` to the worker box, runs `/home/deploy/deploy.sh`.
4. `deploy.sh` writes `/home/deploy/.env` including `INDEXNOW_*`, applies Alembic migrations (0003 creates `indexnow_submission`), runs `crawler sync`, `docker compose up -d` starts all services including the new `indexnow` container.

Graceful degradation: if `INDEXNOW_KEY` is unset, the notifier loop runs but short-circuits at the first line with `log.info("indexnow.disabled")`, sleeping between no-op runs. Deploys succeed regardless of secret presence.

### Smoke tests

```bash
# Key file serves from Vercel
curl -I https://jseek.co/indexnow-key.txt   # HTTP/2 200, body is the key

# Crawler notifier logs (dry-run before flipping the secret on prod)
docker exec deploy-indexnow-1 uv run --no-sync crawler notify-indexnow --dry-run

# Follow the real loop after the secret is set
docker logs -f deploy-indexnow-1 | grep 'indexnow\.'
#  → indexnow.submit.ok status=200 count=N

# Per-URL state
psql -c "SELECT url, last_submitted_at FROM indexnow_submission ORDER BY last_submitted_at DESC LIMIT 10;"

# Bing Webmaster Tools → IndexNow dashboard registers submissions within a few minutes
```

## Metrics

The `indexnow` container listens on `METRICS_PORT=9099`. Prometheus scrape + Grafana dashboard is a TODO — the notifier currently emits only structured log events:

- `indexnow.submit.ok` — successful batch
- `indexnow.submit.rejected` — 4xx (ERROR) or 5xx (WARNING) with status code + truncated response body
- `indexnow.submit.network_error` — WARNING with error type + detail
- `indexnow.nothing_to_submit` — INFO, steady state
- `indexnow.misconfigured` — WARNING, partial env vars set
- `indexnow.disabled` — INFO, key unset
