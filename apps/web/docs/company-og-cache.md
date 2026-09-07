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

A successful publish writes:

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
`industries.csv`. The marker schema records the exact 40-character target Git
revision and the preceding successfully published base revision. The
short-lived `current.json` pointer is updated last and contains the exact same
document as the immutable marker. It is the only mutable object in the
protocol.

Legacy or malformed pointers, a pointer whose immutable marker differs, a
source hash that does not match the recorded revision, and a base revision
that is not an ancestor of the target all fail closed before any PUT. An
explicitly approved manual full rebuild is required to bootstrap the
revision-aware marker schema.

## Off-platform Prewarm

`.github/workflows/prewarm-company-og-cache.yml` runs on site/company renderer
or font/logo changes, weekly reconciliation, and manual dispatch. Production
CSV syncs and crawler deployments also dispatch it as their publication gate.
It:

1. Uploads the site-wide fallback card if its versioned key is missing.
2. Reads companies, localized descriptions, and industries from the same
   versioned CSV sources that feed production.
3. Reads `current.json` and its matching immutable marker to find the last
   successfully published revision.
4. Loads both revisions from Git and compares canonical rendered company
   documents by slug. CSV row order and unused columns do not cause work;
   industry display-name changes naturally affect only referencing companies.
5. Lists the current R2 namespace and adds any missing locale/slug objects to
   the plan, even when their source document is unchanged.
6. Rejects the complete write plan before the first PUT if it exceeds the
   automatic 1,000-write ceiling. Every actual PUT attempt, including retries,
   site-card writes, and marker writes, also consumes the 3,000-attempt runtime
   budget. AWS SDK retries are disabled; the metered three-attempt outer loop
   is the only retry layer. Rendering concurrency is capped at four on both
   automatic and manual runs.
7. Renders added/changed slugs and missing objects with bounded concurrency.
   Removed slugs require no render.
8. Publishes the immutable marker and then `current.json` only after every
   required object succeeds. Bounded canaries never publish markers.

Company PRs merged by the repository's trusted auto-merge workflow use
`GITHUB_TOKEN`, so GitHub intentionally suppresses their recursive `push`
workflows. The same post-merge helper that dispatches production CSV sync also
resolves the exact merged `main` SHA and passes it to the sync workflow. That
workflow dispatches the prewarm and waits for it before publishing the same
SHA. It deliberately does not replace
the web deployment: a new slug uses the deployed Proxy snapshot's bounded
Typesense path until the next genuine web release includes it in the fast
bypass matcher. Neither data consumer may rely solely on a push path filter.

Every actual publisher owns the gate for its complete target snapshot. Both
`sync-data.yml` (push and manual dispatch) and the crawler deploy launch and
await an exact-target prewarm before any mutation. Therefore a failed company
change cannot hitchhike into production on a later unrelated CSV or crawler
commit. Trusted handoffs attach a random token to the dispatched prewarm and
match that exact run, so a same-revision manual canary cannot satisfy the gate.

The production GitHub environment supplies R2 write credentials. The job has
no dependency on the public Typesense tunnel and sends no request through the
deployed OG route, so a prewarm consumes no Vercel Fluid CPU.

Run a bounded incremental canary:

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

## Full Rebuild Controls

Use `COMPANY_OG_RENDERER_VERSION_SALT` to force a new namespace. Store the same
value in Vercel and the GitHub Production environment before deploying so the
metadata redirect and prewarmer remain aligned. Complete the full prewarm
before deploying a new renderer namespace.

There is no automatic force path. A new renderer namespace or migration from a
legacy marker needs one explicitly approved bootstrap. Production-environment
approval remains required, and the workflow caps the plan at 30,000 writes and
all attempts (including retries and markers) at 90,000:

```bash
gh workflow run prewarm-company-og-cache.yml \
  --repo colophon-group/jobseek \
  -f target_revision="$(git rev-parse origin/main)" \
  -f full_rebuild=true \
  -f full_rebuild_confirmation=REBUILD-COMPANY-OG
```

Do not use a full rebuild for company-data changes. Once the revision-aware
baseline exists, ordinary pushes and explicit production-sync dispatches
render only canonical document changes and missing objects.

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

The PNG key is renderer-versioned, not company-data-versioned. Canonically
changed documents overwrite their stable keys; unaffected keys are reused.
This keeps storage bounded and turns a one-company edit into four card PUTs
plus at most two marker PUTs. Renderer changes create a new namespace and must
be bootstrapped manually so an accidental code or workflow push cannot fan out
the full matrix.
