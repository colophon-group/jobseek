# Company OG Image Cache

Company Open Graph images are rendered outside Vercel Functions and cached in
Cloudflare R2. The GitHub prewarmer fills each renderer-version namespace; the
company page metadata points directly at a completed R2 namespace. There is no
request-time company OG Function.

The same job renders the site-wide fallback card to:

```text
og/site/jobseek-v1.png
```

That card is referenced directly by ordinary page metadata. Bump
`SITE_OG_VERSION` whenever its pixels change; the versioned URL is immutable.

## Cache Key

Objects are written to:

```text
og/company/<renderer-version>/<locale>/<slug>.png
```

A successful full prewarm publishes:

```text
og/company/<renderer-version>/_complete/<source-version>.json
og/company/<renderer-version>/_complete/current.json
```

`<renderer-version>` is injected by `next.config.ts` as
`COMPANY_OG_RENDERER_VERSION`. It is a SHA-256 hash, truncated to 16 hex
characters, of the files that can alter pixels:

- `src/lib/og/company-og-card.tsx`
- `src/lib/og/render-company-og.tsx`
- `public/fonts/JetBrainsMono-Bold.ttf`

The version helper is shared by `next.config.ts` and the prewarmer so both
execution environments address the same namespace. Unrelated web deploys keep
reusing the same objects.

`<source-version>` hashes `companies.csv`, `company_descriptions.csv`, and
`industries.csv`. It gates the direct metadata handoff without multiplying the
PNG namespace: source changes overwrite stable renderer keys, then publish a
new immutable completion marker only after the full matrix succeeds. The
short-lived `current.json` pointer is updated last and names that completed
source version. It is the only mutable object in the protocol.

## Off-platform Prewarm

`.github/workflows/prewarm-company-og-cache.yml` runs on site/company renderer
or font/logo changes, company registry changes, weekly reconciliation, and
manual dispatch. It:

1. Uploads the site-wide fallback card if its versioned key is missing.
2. Reads companies, localized descriptions, and industries from the same
   versioned CSV sources that feed production.
3. Lists the current R2 namespace once and skips existing locale/slug objects.
4. Renders each `ImageResponse` card on a GitHub runner.
5. Uploads missing PNGs to R2 with bounded concurrency and retries.
6. Publishes the source-versioned completion marker after a successful full
   matrix; bounded canaries never publish it.
7. Publishes `current.json` only after the immutable marker succeeds.
8. Fails the run if any card or either completion marker cannot be uploaded.

Company PRs merged by the repository's trusted auto-merge workflow use
`GITHUB_TOKEN`, so GitHub intentionally suppresses their recursive `push`
workflows. The same post-merge helper that dispatches production CSV sync also
dispatches this prewarm explicitly and waits for it to succeed before starting
the CSV sync at that exact prewarmed main SHA. It deliberately does not replace
the web deployment: a new slug uses the deployed Proxy snapshot's bounded
Typesense path until the next genuine web release includes it in the fast
bypass matcher. Neither data consumer may rely solely on a push path filter.

The production GitHub environment supplies R2 write credentials. The job has
no dependency on the public Typesense tunnel and sends no request through the
deployed OG route, so a prewarm consumes no Vercel Fluid CPU.

Run a bounded canary before a forced/full reconciliation:

```bash
gh workflow run prewarm-company-og-cache.yml \
  --repo colophon-group/jobseek \
  -f max_companies=25 \
  -f concurrency=4
```

The package script can also be run directly in an environment with the same
scoped credentials:

```bash
pnpm --filter @jobseek/web og:prewarm -- \
  --yes \
  --max-companies 25 \
  --concurrency 4
```

## Runtime Metadata Flow

For a company page:

1. Company metadata reads the renderer's `current.json` pointer through one
   shared five-minute Cache Components entry. After it exists, metadata points
   directly to public R2. Its source-version query key cannot inherit a public
   404 cached before the object was uploaded.
2. During an R2 pointer outage, metadata falls back to the source version
   embedded in the most recent web build and verifies its immutable completion
   marker.
3. If neither completed marker is available, the page inherits the static
   site-wide card. Social previews remain valid without rendering inside a
   Vercel Function.
4. Previously shared `/:lang/company/:slug/opengraph-image-*` URLs receive a
   permanent deployment redirect to the build's versioned R2 object.

## Required Environment

Vercel runtime/build:

- `R2_DOMAIN_URL`
- `COMPANY_OG_RENDERER_VERSION_SALT`

GitHub Production environment:

- the same R2 connection values
- optional `COMPANY_OG_RENDERER_VERSION_SALT` variable

Vercel project env vars are not automatically visible inside `pnpm turbo run
build`. Keep every build-time value in the root `turbo.json` task env allowlist.
Treat Vercel's missing-Turbo-env warning as a release blocker.

## Force Controls

Use `COMPANY_OG_RENDERER_VERSION_SALT` to force a new namespace. Store the same
value in Vercel and the GitHub Production environment before deploying so the
metadata redirect and prewarmer remain aligned. Complete the full prewarm
before deploying a new renderer namespace.

For a namespace-wide repair, manually dispatch the prewarm workflow with
`force=true`; pair it with a CDN purge if already-served objects need immediate
replacement.

## Build Behavior

`next build` never fans out company OG rendering. Routine deploys compute only
the renderer version; the GitHub workflow owns bulk rendering. This keeps
Vercel builds bounded and prevents Typesense/R2 fan-out from affecting deploy
reliability. Company CSV changes advance the R2 `current.json` pointer and do
not require a web deployment; this avoids replacing the Next.js build ID and
cold-starting PPR caches for ingestion-only commits.

## Retention

Renderer-versioned keys intentionally leave old namespaces behind. That makes
rollbacks and CDN revalidation safe, but it also means the R2 bucket would grow
forever without cleanup.

The repo provides `apps/web/script/prune-company-og-cache.ts`, exposed as:

```bash
pnpm --filter @jobseek/web og:prune -- --retain-versions 8 --min-age-days 60
```

The script is dry-run by default. It groups PNGs and their completion markers
under `og/company/` by renderer version, keeps the newest N namespaces, and
only deletes older namespaces whose newest object is past the minimum age.
Pass `--yes` to delete:

```bash
pnpm --filter @jobseek/web og:prune -- \
  --yes \
  --retain-versions 8 \
  --min-age-days 60 \
  --max-delete 20000
```

`.github/workflows/prune-company-og-cache.yml` runs this weekly against the
production R2 bucket with those defaults. The `--max-delete` cap is intentional:
if object volume unexpectedly spikes, the workflow fails instead of deleting
an unbounded number of objects.

## Tradeoff

The PNG key is renderer-versioned, not company-data-versioned. Source-changing
pushes force-replace the current namespace and publish a new source-version
marker; renderer-only changes naturally create a new namespace. Scheduled,
script-only, and workflow-only reconciliations skip existing objects. This
keeps storage bounded while making the direct handoff fail closed and
content-versioned at the CDN layer.
