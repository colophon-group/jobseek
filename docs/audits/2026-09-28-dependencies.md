# Dependency pruning audit — 28 September 2026

Audited `origin/main` at `be97fa7a2c5edf5afe7c604b4a5a049cf2d17aca` in an isolated worktree, after fetching main. The findings below describe that audit snapshot; the implementation status records the subsequent pruning work.

**Recommendation:** remove the proven unused declarations first, retire obsolete cache invalidation and orphan UI, then separate dependencies by executable/service. The strongest bounded replacement candidates are the Nivo funnel, small CSV loaders, and the single Octokit REST operation. Workflow has the largest dependency graph, but replacing its durable execution is a substantially different project.

## Implementation status

Implemented on `fix-crawler/prune-dependencies`, initially based on freshly fetched main at `123a3e928`, then integrated with `e6594efe6` before release:

- Removed all six unused web declarations plus Stripe after the owner confirmed the Paddle replacement. The pnpm installation removed **14 packages**. Removed the stale npm lockfile.
- Deleted the Stripe webhook, 13 orphan UI components, unused search helpers, and the obsolete dynamic company OG cache. Removed exclusive tests and stale mocks/references. Moved the still-used S3 client to web development/operator dependencies.
- Removed crawler Upstash, its settings/helper, and all obsolete platform-stat invalidations, including the phantom sweep deletion. Native Redis queues and web Upstash consumers remain.
- Replaced the three runtime taxonomy resolver CSV loaders and workspace taxonomy/help readers with the existing standard-library CSV helper. Added UTF-8 BOM support and preserved quoted/newline/empty-field behavior. The slim `ws` manifest no longer requires Polars; full crawler sync, enrichment tooling, and Parquet inventory still retain it.
- Bumped the crawler release to `0.13.888`. Billing database history, Paddle, and manual entitlements are preserved.

Validation: production web build and typecheck passed; ESLint passed with one warning in a generated Workflow route. Crawler checks passed **668 tests**, Ruff, and targeted Pyright. All five resolver loader outputs matched main exactly across **1,454 entries**; taxonomy validation and **122 searches** also matched. The slim wheel passed its shipped-module smoke test and actual help/taxonomy commands in a clean Python 3.13 environment containing only its declared dependency closure (41 packages), without Polars or Upstash. That installation used the main lockfile's cached versions; `uv pip check` passed. Both project lockfiles validate.

The full web suite passed **339 files / 2,863 tests** (3 files / 41 tests skipped). A separate workspace CLI run passed **62 tests**. Translation extraction removed 69 orphan messages from each of the four catalogs; all retained translations are unchanged and the translation-coverage gate passed.

Larger product/architecture choices remain proposals: the Nivo presentation, Murmur retention, Workflow executor, separate service images, and further dependency profiles. Octokit/frontmatter replacements and extracting the pure scraper defaults helper are also follow-up work.

The rest of this document is the original audit. Links to subsequently deleted files point to its immutable source revision.

## 1. Immediate web cleanup: six declarations, 13 installed packages

The [web manifest](../../apps/web/package.json) contains these unused direct declarations:

| Declaration | Evidence | Marginal lockfile snapshots removed |
| --- | --- | ---: |
| `@lingui/macro` | No imports. Source uses `@lingui/core/macro` and `@lingui/react/macro`; Babel uses the v6 plugin. The obsolete package installs a separate v5.9.5 core/react/message-utils tree alongside v6.6.0. | 4 |
| `isomorphic-dompurify` | No imports. [The current sanitizer](../../apps/web/src/lib/sanitize.ts) uses a browser template and a DOM allowlist. | 1 |
| `dompurify` in web | No web imports. Keep the separate declaration in trace-viewer, where [Markdown rendering](../../apps/trace-viewer/src/lib/markdown.ts) uses it. | 0 |
| `jose` in web | No code imports; the apparent text match is a generated language name. Other dependencies still require JOSE transitively. | 0 |
| `@nivo/core` | No direct imports. `@nivo/sankey` already declares it as a dependency, not a peer requirement. | 0 |
| `@testing-library/jest-dom` | No setup import, configuration reference, or matcher usage found. Tests use Vitest assertions. | 8 |

**Rehearsal completed:** temporarily removed all six declarations, ran `pnpm install --offline --ignore-scripts`, and pnpm reported **13 packages removed**. With those declarations absent:

- Lingui catalog compilation passed.
- A real Babel transformation of `@lingui/core/macro` using the configured Lingui v6 plugin passed.
- Five existing test files passed: sanitizer, blog content, company request, and both Sankey suites — **39 tests**.

The first test attempt needed the workspace MCP package built because lifecycle scripts were deliberately skipped. After `pnpm --filter @jseek/mcp-server build`, all five suites passed. Original manifests and lockfile were restored and their frozen offline installation succeeded. This was not a full production build or full test run.

Also remove the obsolete root [package-lock.json](https://github.com/colophon-group/jobseek/blob/be97fa7a2c5edf5afe7c604b4a5a049cf2d17aca/package-lock.json): it describes only eight entries and an older Turbo requirement, while the root pins pnpm and the checked workflows use pnpm. This is maintenance cleanup, not application bundle reduction. Do not remove the pnpm lockfile or security overrides merely because a direct declaration disappears.

## 2. Dead code and obsolete behavior

### Crawler Upstash invalidation can be retired

[shared/redis.py](https://github.com/colophon-group/jobseek/blob/be97fa7a2c5edf5afe7c604b4a5a049cf2d17aca/apps/crawler/src/shared/redis.py) imports `upstash-redis`. Its source consumer is the compatibility export in [batch.py](../../apps/crawler/src/batch.py), used by three calls in [processing/board.py](../../apps/crawler/src/processing/board.py) to delete `cache:platform-stats`.

There is no remaining web reader or writer for that Redis key. [getSiteStats](../../apps/web/src/lib/actions/stats.ts) explicitly documents its migration to Next `use cache` in #2884. [phantom_sweep.py](../../apps/crawler/src/phantom_sweep.py) also deletes that obsolete key, using the separate native Redis queue client.

Remove those obsolete deletions, the Upstash helper/export/settings, corresponding mocks/tests, and the Python `upstash-redis` requirement together. This removes one Python package and redundant network work. **Keep** the crawler's native `redis` package and the web's Upstash packages: queues, rate limiting, and other web caches still use them.

### Thirteen UI components have no production import path

These components total **2,762 source lines**. They have no production path from the web app/config/script roots in the import graph; remaining references are tests, documentation, definitions, or other components within this orphan set.

| Area | Files relative to `apps/web/src/components/` |
| --- | --- |
| My jobs | `my-jobs/my-job-detail-panel.tsx`, `my-jobs/quick-actions.tsx`, `my-jobs/salary-override.tsx`, `my-jobs/status-badge.tsx` |
| Search | `search/empty-state.tsx`, `search/filter-bar.tsx`, `search/keyword-pills.tsx`, `search/location-modal.tsx`, `search/location-pills.tsx` |
| Upgrade | `ui/upgrade-modal.tsx` |
| Watchlists | `watchlist/company-selector.tsx`, `watchlist/time-range-selector.tsx`, `watchlist/watchlist-filter-editor.tsx` |

Treat this as a coherent deletion candidate, including tests that only exercise these components. Existing tests are not evidence that a feature is reachable. None of these deletions alone removes Radix, Lucide, or React: current UI still uses them.

Other source-only cleanup candidates:

- [search/pg-filters.ts](https://github.com/colophon-group/jobseek/blob/be97fa7a2c5edf5afe7c604b4a5a049cf2d17aca/apps/web/src/lib/search/pg-filters.ts): `localesOrNoneClause` has no production callers; several tests retain obsolete mocks.
- [search/explore-degraded.ts](https://github.com/colophon-group/jobseek/blob/be97fa7a2c5edf5afe7c604b4a5a049cf2d17aca/apps/web/src/lib/search/explore-degraded.ts): `buildUnavailableExploreData` has no callers.
- [og/company-og-cache.ts](https://github.com/colophon-group/jobseek/blob/be97fa7a2c5edf5afe7c604b4a5a049cf2d17aca/apps/web/src/lib/og/company-og-cache.ts): old dynamic OG cache read/write helpers are referenced only by their tests. Current prewarming has its own implementation.
- [crawler/indexnow.py](../../apps/crawler/src/indexnow.py) and its CLI branch: explicitly retired in the [SEO runbook](../13-seo-and-indexnow.md), still manually callable. Removal saves code/tests, not an exclusive dependency. Keep the separate active blog IndexNow workflow.
- [crawler/bootstrap.py](../../apps/crawler/src/bootstrap.py): explicitly retained non-executable Supabase rollback helpers, referenced by tests. Remove only after formally ending that retention purpose; no exclusive dependency saving.

Do not blindly delete every graph orphan. [actions/watchlist-page-data.ts](../../apps/web/src/lib/actions/watchlist-page-data.ts) intentionally holds fail-closed compatibility handlers. `src/db/migrate.ts` and `seed.ts` are package-script entry points. The old company OG renderer is also a content-hash input in [company-og-renderer-version.ts](../../apps/web/src/lib/og/company-og-renderer-version.ts); changing it affects cache namespaces and prewarming.

## 3. Inactive or transitional features

| Surface | Repository evidence | Recommendation |
| --- | --- | --- |
| Murmur shim and pipeline tooling | [Transition document](../16-murmur-codex-mcp-transition.md) explicitly says services/deployment are paused. Nevertheless, two pnpm workspaces remain; CI tests, builds, and collects shim coverage. Together they contain 67 tracked files and 8,468 JS/TS lines including tests. | Decide whether to retire or move them outside default install/build/CI. Removing both workspace roots makes 17 current lockfile snapshots unreachable, including the second Next version, 16.3.4. Also review web trigger/status routes and root scripts. Preserve the host's active Lightpanda deployments and shared deployment locking. |
| Legacy Stripe webhook | `stripe` has one production importer: [api/stripe/webhook](https://github.com/colophon-group/jobseek/blob/be97fa7a2c5edf5afe7c604b4a5a049cf2d17aca/apps/web/app/api/stripe/webhook/route.ts). Current billing actions use Paddle. | Strong retirement candidate after verifying no live Stripe subscriptions or webhook deliveries still depend on it. Remove the route and SDK together; one exclusive snapshot. Keep the `subscription` table: [paid-entitlement.ts](../../apps/web/src/lib/paid-entitlement.ts) still uses it for manual grants. |
| Paddle checkout | [Paddle runbook](../../apps/web/docs/paddle.md) records fresh September 27 setup and a disabled checkout flag. Checkout, portal, webhook, cancellation, and entitlements are wired. | This is a feature under rollout, not dead code. Retain unless the product direction changes. |
| AI-filter catch-up / Workflow | Two execution flags gate it, but the reconcile route calls a real durable workflow. | Do not infer inactivity from default-false flags. Assess separately from the dependency count below. |

No production credentials, traffic, database rows, or live feature flag values were inspected. Deployment status here is based on checked-in source/runbooks, not a new production observation.

## 4. Highest-value packaging change: separate crawler capabilities

[pyproject.toml](../../apps/crawler/pyproject.toml) has 32 base dependencies, resolving to 75 Python package names excluding the project and development/enrichment extras. [The Docker base](../../apps/crawler/Dockerfile) installs every base dependency plus antiword and Tesseract. Its “no Playwright” comment describes the missing browser binaries; the Python Playwright package is still installed.

The most obvious service boundary is the **Go R2 drain**. [Compose](../../apps/crawler/docker-compose.yml) launches and health-checks `/usr/local/bin/go-r2-drain`, but gives it the shared crawler image containing Python, all Python dependencies, OCR tools, data, and the other Go executables. A dedicated Go runtime image could omit those application packages from the drain service. Preserve CA certificates, required runtime files, credential delivery, health checks, and the deployment/rollback image contracts. Shared Docker layers mean this is not a claim of equivalent fleet-wide disk savings.

Do not apply that change blindly to the exporter: [exporter-entrypoint.sh](../../apps/crawler/scripts/exporter-entrypoint.sh) still selects between Python and Go ownership.

For Python tooling, introduce explicit capability extras/install profiles and make image/CI/runner installation choose them:

| Capability | Dependencies to isolate | Important constraint |
| --- | --- | --- |
| Gold labeling | `beautifulsoup4`, `huggingface-hub`, `jinja2`, appropriate validation dependencies | HF also uploads workspace traces; Jinja renders `ws` instructions. `jsonschema` also serves migration/runtime-cost gates, so it is not labeller-only. |
| Company image tooling | `boto3`, `cairosvg`, image/OCR dependencies as needed | Boto3's only direct importer is CI's `image_sync.py`, **but runtime `description_store.py` imports botocore**. Declare/retain botocore explicitly before moving Boto3. This initially saves Boto3/s3transfer, not the whole AWS dependency tree. |
| PDF/OCR worker | `pypdf`, `pypdfium2`, `pytesseract`, Tesseract language data, antiword where required | 49 registry rows select the PDF scraper; 8 contain OCR configuration, including nested document fallbacks. This is used capability. Route those jobs to a capable worker before slimming other workers. Registry counts do not prove live activity. |
| Browser worker | `playwright` and browser binaries | Some monitors import Playwright types at module load. Refactor import/registry boundaries before omitting the Python package from HTTP-only workers. |
| Setup/operator tools | Click, Jinja, HF, image tools, migration tools, taxonomy tools as needed | Preserve the published `ws` wheel and clean-environment smoke coverage. Moving dependencies between sections alone cannot fix eager imports. |

The [slim ws manifest](../../apps/crawler/ws-package/pyproject.toml) documents its current coupling explicitly: `ws run scraper` imports `_apply_defaults` from the large processing module, pulling in CPU resolvers, asyncpg, metrics, and Polars. Moving this pure helper to a small shared module and untangling eager imports is a better first move than deleting libraries or duplicating logic. Review other probe paths before removing any wheel requirement.

## 5. Replacement candidates, ranked by effort and usefulness

Counts below are marginal **pnpm lockfile snapshots** under the current resolved graph, including peer variants and optional platform packages. They are not download bytes, browser bundle size, or production memory. Scenarios are independent and must not be added together.

| Candidate | Potential saving | Assessment |
| --- | ---: | --- |
| Nivo Sankey → existing stage summary or a small bespoke funnel | 42 snapshots | Best UI simplification candidate. Only [sankey-funnel.tsx](../../apps/web/src/components/my-jobs/sankey-funnel.tsx) imports it. That component already renders an accessible stage/outcome summary on small screens. Reusing that presentation on desktop removes Nivo, React Spring, and its exclusive D3 tree, with a deliberate visual change. If preserving Sankey flows, retain a focused layout library and write only the rendering; savings would be smaller. |
| Polars CSV-only loaders → standard `csv.DictReader` | 2 Python packages where Polars can be omitted | [Occupation](../../apps/crawler/src/core/occupation_resolve.py), [seniority](../../apps/crawler/src/core/seniority_resolve.py), and [technology](../../apps/crawler/src/core/technology_resolve.py) resolvers use Polars to read rows into Python dictionaries. Preserve quoting, empty/null behavior, Unicode aliases, and ordering. Keep Polars for ATS inventory Parquet scanning; sync/taxonomy tools need separate review. This can slim the ws/worker profile without pretending Polars is unused repo-wide. [Python CSV documentation](https://docs.python.org/3/library/csv.html). |
| `@octokit/rest` → one typed HTTP operation, retaining `@octokit/auth-app` | 15 snapshots | [request-company.ts](../../apps/web/src/lib/actions/request-company.ts) uses only `issues.create`. Keep app/installation authentication and token caching, and issue the request through a small adapter with deadlines and response/error handling. [Octokit supports standalone installation authentication](https://github.com/octokit/auth-app.js/blob/main/README.md); [Node supplies fetch](https://nodejs.org/api/globals.html#fetch). Do not hand-roll JWT handling to remove the auth dependency too. |
| `gray-matter` → bounded frontmatter envelope extraction, retaining `js-yaml` | 6 snapshots | [blog.ts](../../apps/web/src/lib/blog.ts) already supplies a custom current-version YAML engine because gray-matter's default API is obsolete. A small extractor for this repository's delimiter format can remove gray-matter and its extra YAML v4 tree. Preserve BOM/CRLF, missing/empty frontmatter, delimiter handling, multiline YAML, and exact body text. Keep YAML parsing in the existing library. |
| Workflow → another durable executor | 291 snapshots | Largest graph, highest semantic risk. The [workflow](../../apps/web/workflows/ai-filter-catchup/index.ts) is a bounded loop of at most ten steps, but those steps are durable. Existing [catch-up service](../../apps/web/src/lib/ai-filter/catchup-service.ts) already has leases/checkpoints; that helps a redesign but does not replace durable dispatch, retry scheduling, crash recovery, or observability. Keep for now unless the team commits to owning that executor or retires catch-up. A detached promise or request-lifetime loop is not equivalent. |

The Workflow package's installed manifest pulls in CLI and adapters for Astro, Next, Nest, Nitro, Nuxt, SvelteKit, and Rollup. That explains much of its install footprint; it does not mean those adapters enter the browser bundle. Investigate supported narrower packaging before considering a rewrite.

The Nivo component is already dynamically loaded with `ssr: false`; its removal benefits users loading that chart, not necessarily initial site navigation. Measure a production build before assigning byte savings.

Other useful scope corrections: after removing the unused dynamic OG cache helper, `@aws-sdk/client-s3` is used by prewarm/prune scripts and tests. Moving it to development/operator tooling is plausible, but deleting it would break those active workflows. Tailwind/PostCSS are build tooling. Reclassification alone does not reduce a normal full workspace installation.

## 6. Dependencies that should survive this audit

- **Authentication, cryptography, sanitization, and untrusted HTML parsing:** keep the active implementations/libraries. Removing unused DOMPurify declarations does not justify another sanitizer rewrite; trace-viewer still needs its sanitizer.
- **`psycopg2-binary`:** no explicit Python import, but Alembic's [SQLAlchemy engine construction](../../apps/crawler/src/migrations/env.py) uses the PostgreSQL driver. Removing it without changing the migration engine breaks deployment.
- **`tzdata`:** consumed indirectly by `zoneinfo`; monitors use `Europe/Zurich` and `Asia/Seoul`.
- **`sharp`, React DOM, Lingui loader/plugin, coverage tooling:** framework/config/CLI use can be invisible to ordinary import scans.
- **Python Typesense/Redis/asyncpg and migrated monitors:** Go implementations coexist with supported Python paths, operator commands, and rollback ownership. A Go default is not proof of full retirement.
- **LLM SDK extras:** OpenAI/Anthropic/Google SDKs already live in the optional `enrich` extra. Removing them does not slim the current default Docker installation.
- **Go libraries:** reviewed 22 module manifests; many monitor executables use only the standard library. No similarly compelling unconditional Go package deletion emerged. Prioritize service image separation over rewriting database, Redis, protobuf, or HTML libraries.

## Suggested change sequence

1. Remove the six rehearsed web declarations and stale npm lock; run full web checks before merging.
2. Remove obsolete crawler platform-stat invalidation/Upstash and the orphan UI/helpers, with their obsolete tests and documentation references.
3. Decouple the ws pure helpers, replace CSV-only Polars loaders, and separate labeling/image/operator dependency profiles. Validate fresh installs rather than an existing full development environment.
4. Split the Go drain image, preserving deployment/rollback contracts; measure actual image and build differences.
5. Decide whether to simplify the funnel, archive Murmur from default builds, and retire the Stripe endpoint. Each is a separate product/retention decision.
6. Consider the small Octokit/frontmatter adapters. Treat Workflow replacement as its own architectural decision.

## Method and limits

Inspected six JavaScript workspace manifests (root included), three Python manifests, 22 Go module manifests, Docker/Compose and CI entry points, and all 7,879 board registry rows. Parsed tracked JS/TS imports, re-exports, `require`, and literal dynamic imports using the installed TypeScript parser; traced production reachability and manually checked candidates against scripts, configuration, tests, and retained compatibility behavior. Parsed Python imports with `ast` and inspected indirect/lazy dependencies.

The pnpm graph contained 1,349 reachable snapshots; the graph traversal resolved every encountered edge. Marginal counts retain all other workspace roots and follow both dependency and optional-dependency edges. Peer re-resolution can change those estimates; only the six-package cleanup was rehearsed with pnpm itself. Python counts came from the locked base dependency closure.

This audit does not establish exhaustive dead exports, production feature inactivity, install-size savings, security status, or full build compatibility. At the original audit stage, only the focused removal rehearsal had been executed. See the implementation status above for completed deletions, replacements, and validation; image changes remain proposals.
