# Jev for search filter mapping: latency experiment

**Date:** 2026-09-23

**Base:** `origin/main` at `eb40e2b2a`

**Verdict:** Jev is plausible for a single, submit-time decision if an additional roughly 0.3–0.5 seconds is acceptable. It would materially slow the current cache-warm parser: a typical repeated query took 30 ms in the parser, versus 280 ms median for the additional Jev call. The measurements do **not** justify replacing the shared parser unconditionally. A direct replacement can invoke Jev twice during one search and has no measured production tail latency or mapping-quality result.

## What was measured

The search bar calls `parseSearchFilters` before navigating. That service tokenizes free text, queries Typesense taxonomy suggestions in parallel, and applies exact-match rules to choose location, occupation, seniority, technology, work-mode, or remaining keywords. Explore and company page loaders also call the same service after navigation. The first call is on the interactive submit path; the second may process remaining `q` keywords and explicit filter slugs.

I sent synthetic search strings to TypeSafe's direct `POST /v1/systemone` endpoint with the pinned `jev-1.13.0` model. Each word was one Choice question, with predefined filter options and a `keyword` option. All questions for a search were batched into one request. This approximates the decision step *after* candidate filters are available. It does not perform candidate generation or verify the chosen mappings. TypeSafe's [API schema](https://api.typesafe.ai/openapi.json) confirms that one request can contain multiple named questions and returns answers and token usage.

The probe ran from a local macOS machine in Switzerland using Node's `fetch` and the existing TypeSafe credential. It made one warm-up call, then 12 sequential repetitions of each fixture, rotating fixture order (36 measured Jev calls). It recorded complete HTTP response time, including network transfer and JSON parsing. All 36 returned HTTP 200 and the requested pinned model, with no retry. This sample tests light sequential load only.

| Fixture | Choice questions | Input tokens/call | Jev median | Jev p95* | Observed range |
|---|---:|---:|---:|---:|---:|
| `remote engineer` | 2 | 443 | 285 ms | 314 ms | 245–314 ms |
| `senior Python developer Zurich remote` | 5 | 697 | 280 ms | 472 ms | 250–472 ms |
| `hybrid staff backend engineer Rust Kubernetes Berlin` | 7 | 878 | 281 ms | 364 ms | 252–364 ms |
| **All measured calls** | 2–7 | 443–878 | **285 ms** | **439 ms** | **245–472 ms** |

\*With 12 samples per fixture, p95 is effectively the largest observation; these are sample descriptions, not service-level guarantees. The initial warm-up was 374 ms. A separate preliminary call measured 518 ms, outside the 36-call sample. TypeSafe's own [launch post](https://typesafe.ai/blog/introducing-system-one-models-and-jev) quotes 70–500 ms but notes those published measurements were generally made from West Coast laptops; the figures above are this experiment's measurements.

### Actual parser and Typesense path

I ran the actual `parseSearchFilters` service through a temporary local Next.js route. This preserves its parallel Typesense calls and `use cache` behavior. The route measured time **inside** `parseSearchFilters`; the client also measured the full HTTP round trip. I started Next.js 16.3.6 in development mode with the existing web Typesense credential, cleared the local dev cache by moving its directory aside, and made one call per fixture followed by 12 repeated calls each. The route was removed after the experiment; its source is retained as a reproduction template. All 39 responses were HTTP 200. The typical and long queries returned every expected location, occupation, seniority, technology, and work-mode filter in every call; the short query returned its expected remote work mode.

| Fixture | First parser call | Repeated parser median | Repeated parser p95 | Repeated HTTP median |
|---|---:|---:|---:|---:|
| Short | 476 ms | 12 ms | 15 ms | 26 ms |
| Typical | 264 ms | 30 ms | 37 ms | 39 ms |
| Long | 273 ms | 42 ms | 80 ms | 53 ms |

To avoid relying on one first call per shape, I restarted the dev server with another empty dev cache and submitted 12 distinct queries with different city names, 3 seconds apart. Every query resolved its expected city; some returned a longer hierarchical slug, which the validation accepts. The parser time for these first lookups was **146 ms median, 483 ms p95**, range 104–483 ms. Terms besides the city can overlap with earlier queries in the same run, so this is a first-query, mixed-cache sample rather than a guarantee that every lookup was cold. The first HTTP round trip also included Next.js route compilation; the parser timer excludes that compilation.

I had initially attempted to benchmark the Typesense fan-out with standalone parallel HTTP requests. That probe received HTTP 429. It bypassed the application's cache, so it is not used for the comparison above. The app-level runs completed and returned the expected structured filters. There is still no production Vercel measurement, and the temporary route does not include the search bar's server-action transport or result rendering.

For completeness, an in-process microbenchmark of the actual parser with mocked lookup functions gave 0.019–0.056 ms medians and 0.044–0.135 ms p95 values across the three fixtures. That isolates local parsing work; the app-level results above are the relevant comparison because they include Typesense access and caching.

## Interpretation

- A Jev decision made **after** taxonomy lookup adds about 285 ms median and 439 ms p95 in this sample. On the typical repeated query, the measured parser median was 30 ms and Jev median was 280 ms. Their serial sum is roughly 310 ms before server-action transport, navigation, and rendering. On the first typical query, the observed parser time was 264 ms, implying roughly 544 ms with a median Jev call. Those sums are inferences from separate measurements, not a combined end-to-end sample.
- The cache-warm parser is already quick. Jev would dominate its latency; the relative impact is much smaller for a first lookup that takes a few hundred milliseconds, but the additional wait is still user-visible.
- If Jev is inserted into the shared `parseSearchFilters` function, the search bar's pre-navigation call and the page's post-navigation call may each invoke it. Two serial calls would add about 570 ms at the observed median if neither is skipped or reused. That number is an inference from the call sites and measured median, not a two-call end-to-end measurement.
- The observed Jev latency was nearly flat from two to seven Choice questions. Batching decisions into one request is materially preferable to one request per term; the experiment did not test higher cardinality, concurrent traffic, or production-region tails.
- The existing Jev client for the separate narrow-results feature has a 10-second timeout and one retry. Reusing that policy on the blocking search-submit path could produce a much longer wait during failure. A search integration would need its own short deadline and heuristic fallback.
- The experiment supplied candidate options by hand. Jev chose `keyword` for `developer` in all 12 typical-query repetitions despite a Software Engineer option. That is one observation, not a quality verdict; a labelled query set with actual Typesense candidates is required before changing mappings.

## Recommendation

Treat Jev as a **candidate for a bounded, submit-only pilot** if the product accepts roughly half a second of additional wait at the observed tail. The current parser's warm path is much faster, so unconditional synchronous replacement would be a noticeable regression. Keep explicit URL filters deterministic. Avoid a call on every keystroke and avoid placing a paid network call into every shared-parser invocation. Measure from the deployed Vercel region with real candidate generation, including p95/p99, timeout rate, and the whole submit-to-results interval; compare mapping quality against a labelled query set before replacing heuristics. If the additional-latency budget is below 300 ms at p95, this experiment does not support Jev on the blocking path.

## Reproduction and data

- [Jev latency probe](../../scripts/experiments/jev-search-filter-latency.mjs) — accepts local untracked env-file paths via `--typesafe-env` and optional `--typesense-env`; no credentials are stored in the output. The measured run used `--typesense-mode off --reps 12`.
- [Raw Jev observations](jev-search-filter-latency-2026-09-23.json) — all 36 latencies, usages, and choices.
- [Temporary local route template](../../scripts/experiments/filter-latency-route.ts) — copy to `apps/web/app/api/experiments/filter-latency/route.ts` in an isolated worktree, start `next dev -p 3150` with the web env file, and remove the route after measurement. It responds only on localhost in development mode.
- [Parser and Typesense probe](../../scripts/experiments/measure-search-filter-service.mjs) with [raw first/repeated results](search-filter-service-typesense-2026-09-23.json) — one first call and 12 repeated calls per fixture.
- [Distinct-query probe](../../scripts/experiments/measure-search-filter-first-lookups.mjs) with [raw results](search-filter-first-lookups-2026-09-23.json) — 12 first queries after a fresh dev cache.
- [Parser microbenchmark](../../apps/web/src/lib/actions/__tests__/search-input-latency.test.ts) — opt-in via `RUN_SEARCH_INPUT_LATENCY_BENCH=1`; [captured output](heuristic-matching-latency-2026-09-23.txt).

The 36 Jev calls used 24,216 input tokens. At TypeSafe's [published input price](https://typesafe.ai/blog/introducing-system-one-models-and-jev) of $0.042 per million tokens, that is about $0.00102 for this run; output tokens are currently free. Cost was not the decision criterion here.
