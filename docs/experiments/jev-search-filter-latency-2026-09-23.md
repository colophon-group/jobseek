# Jev for search filter mapping: latency experiment

**Date:** 2026-09-23

**Base:** `origin/main` at `eb40e2b2a`
**Verdict:** Jev is plausible for a single, submit-time decision, provided an additional roughly 0.3–0.5 seconds is acceptable. The measurements do **not** justify replacing the shared parser unconditionally. A direct replacement can invoke Jev twice during one search and has no measured production tail latency or mapping-quality result.

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

For a local comparison, I ran the actual `parseSearchFilters` code 2,000 times per fixture in Vitest, with suggestion and slug-resolution functions mocked to immediately return representative values. After 100 warm-ups per fixture, its measured medians were 0.019, 0.051, and 0.056 ms; p95 values were 0.044, 0.130, and 0.135 ms for short, typical, and long respectively. This isolates parser and promise-processing work. It **excludes** Typesense, the server action, navigation, and rendering, so it is not an end-to-end baseline.

I attempted a direct, parallel Typesense request fan-out mirroring the parser's single-word and occupation pair/triplet candidates. That run received HTTP 429 and was stopped. It cannot supply a valid baseline for the suggestion stage. In particular, the probe bypassed the app's `use cache` layer and came from a different network location than Vercel; the 429 is not evidence that production search is rate-limited.

## Interpretation

- A Jev decision made **after** taxonomy lookup adds the measured call time to the current critical path: about 285 ms median and 439 ms p95 across this small sample. The full search-submit latency remains unmeasured because the Typesense fan-out probe was rate-limited.
- If Jev is inserted into the shared `parseSearchFilters` function, the search bar's pre-navigation call and the page's post-navigation call may each invoke it. Two serial calls would add about 570 ms at the observed median if neither is skipped or reused. That number is an inference from the call sites and measured median, not a two-call end-to-end measurement.
- The observed Jev latency was nearly flat from two to seven Choice questions. Batching decisions into one request is materially preferable to one request per term; the experiment did not test higher cardinality, concurrent traffic, or production-region tails.
- The existing Jev client for the separate narrow-results feature has a 10-second timeout and one retry. Reusing that policy on the blocking search-submit path could produce a much longer wait during failure. A search integration would need its own short deadline and heuristic fallback.
- The experiment supplied candidate options by hand. Jev chose `keyword` for `developer` in all 12 typical-query repetitions despite a Software Engineer option. That is one observation, not a quality verdict; a labelled query set with actual Typesense candidates is required before changing mappings.

## Recommendation

Treat Jev as a **candidate for a bounded, submit-only pilot** if the product accepts roughly half a second of additional wait at the observed tail. Keep explicit URL filters deterministic. Avoid a call on every keystroke and avoid placing a paid network call into every shared-parser invocation. Measure from the deployed Vercel region with real candidate generation, including p95/p99, timeout rate, and the whole submit-to-results interval; compare mapping quality against a labelled query set before replacing heuristics. If the additional-latency budget is below 300 ms at p95, this experiment does not support Jev on the blocking path.

## Reproduction and data

- [Jev latency probe](../../scripts/experiments/jev-search-filter-latency.mjs) — accepts local untracked env-file paths via `--typesafe-env` and optional `--typesense-env`; no credentials are stored in the output. The measured run used `--typesense-mode off --reps 12`.
- [Raw Jev observations](jev-search-filter-latency-2026-09-23.json) — all 36 latencies, usages, and choices.
- [Parser microbenchmark](../../apps/web/src/lib/actions/__tests__/search-input-latency.test.ts) — opt-in via `RUN_SEARCH_INPUT_LATENCY_BENCH=1`; [captured output](heuristic-matching-latency-2026-09-23.txt).

The 36 Jev calls used 24,216 input tokens. At TypeSafe's [published input price](https://typesafe.ai/blog/introducing-system-one-models-and-jev) of $0.042 per million tokens, that is about $0.00102 for this run; output tokens are currently free. Cost was not the decision criterion here.
