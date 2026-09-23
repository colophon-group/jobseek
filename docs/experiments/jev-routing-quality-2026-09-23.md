# Jev routing for search filters: quality and latency experiment

**Follow-up:** [Occupation-diverse dataset, tuned prompt, and fresh holdout](jev-routing-diverse-followup-2026-09-24.md).

**Started:** 2026-09-23; completed 2026-09-24

**Code base:** `origin/main` at `eb40e2b2a`, isolated worktree

**Scope:** experiment and report; no production search behavior changed

## Result

Jev can improve the interpretation of entered search text, especially for unrelated requests, multiword places, and some misspellings. The best prompt plus the app's Typesense suggestion services matched **20 of 28 held-out human target configurations** (71%), compared with **9 of 28** (32%) from the current parser. Its held-out filter F1 was **0.863**, compared with **0.590**. This is promising as a *proposed configuration* that users can inspect; it is not accurate enough to silently apply as the only interpretation of every Enter press.

The measured Jev-plus-normalization path took **496 ms median, 715 ms p95** on the 28 held-out queries from a local machine in Switzerland. The current parser's repeated-call median was **80 ms** on those queries. The Jev path therefore adds a visible pause at submission. These are local development and sequential-request results, not production-region p95 or browser submit-to-results timing.

## Dataset and labels

I wrote **84 plausible search-box inputs**, 12 in each of seven groups: atomic, terse abbreviations, verbose, misspelled, multiword, unrelated, and German/French/Italian. The first eight in each group (56 total) were used for prompt and threshold tuning; the final four (28 total) were held out until the final `minimal2` prompt and 0.6/longest-first routing rule were fixed. The inputs are synthetic editorial examples, **not sampled user logs**. They overrepresent edge cases and were labeled by one annotator, so neither the accuracy nor latency distribution is a production estimate.

The current app's actual `parseSearchFilters` ran through a temporary local Next.js route connected to the configured Typesense service. Every query was repeated until two consecutive structured answers agreed: **84/84 stable**, with all expected atomic sanity checks passing. Those outputs are the **current-system baseline**. They are not human gold. For example, the baseline interprets “San Francisco software engineer” as `san` plus a `Francisco` keyword, and “remote control batteries” as remote work.

I separately labeled **164 intended filter spans** across the 84 queries. Unmarked words remain keywords. The labels name the text and filter dimension, without copying the parser's output. Of those spans, 136 require taxonomy/location normalization, and 28 are work-mode or employment-type decisions. I ran the labeled taxonomy terms through the app's actual Typesense suggestion services. **133/136 returned at least one suggestion**, but an available suggestion is not necessarily correct. Three returned none: `pyhton` as Python, `kubernets` as Kubernetes, and German `Softwareentwickler` as Software Engineer. The normalized human target uses explicitly listed manual slug corrections for ten missing, ambiguous, or wrong-ranked suggestions; those corrections are visible in the scoring script and were never passed to Jev.

## Routing experiment

Each input became one Jev `jev-1.13.0` request containing Choice questions for its one-, two-, and three-word spans. Each question could choose keyword, location, occupation, seniority, technology, remote, hybrid, onsite, or employment type. The postprocessor selects non-overlapping spans. Then the app's suggestion services normalize the selected taxonomy terms. This tests the proposed **route terms first, normalize second** design. The raw runs record each Jev answer, probability, token usage, request latency, real suggestion list, and lookup latency.

Three prompt revisions were tested on the 56-query tuning split. The final revision gives Jev explicit rules to keep separate filter concepts in separate spans and to use multiword spans only for a single title or place. The routing cutoff and overlap policy were selected on tuning only. No prompt or cutoff was changed after opening the held-out results.

| Prompt on tuning split | Exact intended spans | Span F1 |
|---|---:|---:|
| Natural semantic routing | 19/56 | 0.420 |
| Literal parser mimicry | 26/56 | 0.689 |
| Minimal filter spans | 43/56 | 0.887 |
| Final `minimal2` prompt | **45/56** | **0.915** |
| Final prompt, fixed rule on holdout | **17/28** | **0.820** |

For a complete configuration I compared two normalization rules: accept only an exact name/alias, or default to the first Typesense suggestion when no exact match exists. The latter was selected on tuning. The reference configuration includes keywords, locations, occupations, seniorities, technologies, work mode, and employment type; array order is ignored for exact scoring. Manual human-target slug corrections are applied to the reference only.

| Split and method | Exact complete configuration | Filter precision | Filter recall | Filter F1 |
|---|---:|---:|---:|---:|
| Tuning, current parser | 28/56 | 0.955 | 0.733 | 0.829 |
| Tuning, Jev + exact suggestion | 36/56 | 0.979 | 0.802 | 0.882 |
| Tuning, Jev + first suggestion | **40/56** | 0.919 | 0.879 | 0.899 |
| Holdout, current parser | 9/28 | 0.767 | 0.479 | 0.590 |
| Holdout, Jev + exact suggestion | 16/28 | 0.944 | 0.708 | 0.810 |
| Holdout, Jev + first suggestion | **20/28** | 0.872 | 0.854 | 0.863 |

The apparent gain is **11 more exact held-out configurations** on this deliberately difficult sample. It is partly due to Jev correctly leaving unrelated queries as keywords: the current parser maps literal words such as `remote`, `senior`, and `Paris` even in unrelated requests. Jev also gets the full San Francisco and Los Angeles locations and interprets `remtoe` as remote work. The human target for ambiguous “New York” is the city; Jev plus the first Typesense hit selects the state, showing why the individual suggestion should remain visible.

### Remaining errors and normalization limits

- The final router still combined or split spans incorrectly on the holdout: `backend developer` became just `developer`; `onsite recruiter` became one occupation phrase; `frontend dev` was split into a technology and a generic developer occupation. It labeled 17/28 complete span sets exactly, even though normalization and fallback keywords yielded 20/28 exact final configurations.
- Blindly accepting the first suggestion can produce a wrong **filter**, not merely a different label. For `onsite recruiter`, Typesense offered **Medical Representative** through an alias beginning “Onsite …”; for `policy analyst`, the first suggestion was Business Analyst; for `finance analyst`, it was Finance Manager. These were scored as errors against human gold. A production default must verify candidate quality, present ambiguous suggestions, or leave the phrase as a keyword.
- Typesense did not suggest an item for `pyhton`, `kubernets`, or `Softwareentwickler` in the corresponding category. Other top hits were wrong or ambiguous: `software engineering` returned Engineering Manager rather than Software Engineer; `Data Engineer` in German returned Analytics Engineer before Data Engineer; `berln` returned State of Berlin before Berlin. Jev's category decision alone cannot repair these normalizer gaps.
- The normalized target requires editorial judgment for place granularity. “New York” can mean city or state; Zürich in a local language can return canton or city. The ten explicit slug overrides keep the metric auditable but also expose this source of subjectivity.

## Delay

All timings are client-observed completed requests, sequential from this local worktree. Jev timing includes its HTTPS call and JSON response. Normalization timing includes the local Next.js route plus the app's Typesense-backed suggestion functions. Their sum is the measured experimental route-to-configuration interval, without search navigation or result rendering. No Jev call failed in the final tuning or holdout runs.

| Final prompt | Queries | Jev median / p95 | Normalization median / p95 | Combined median / p95 | Combined max |
|---|---:|---:|---:|---:|---:|
| Tuning | 56 | 445 / 589 ms | 22 / 136 ms | 477 / 707 ms | 1,119 ms |
| Holdout | 28 | 407 / 566 ms | 88 / 185 ms | **496 / 715 ms** | 1,419 ms |

For comparison, the actual parser on all 84 inputs took **60 ms median, 539 ms p95** on its repeated call through the development route; its first call per input was **348 ms median, 850 ms p95**. For the 28 holdout queries alone, repeated parser time was **80 ms median, 641 ms p95**. The wide tail reflects development-server and Typesense cache variability; the previous focused benchmark found 30 ms median for repeated typical queries. Percentiles from 28 or 84 sequential synthetic requests are descriptive, not an SLA. The final Jev prompt asked a median of six Choice questions and as many as 33, with one Jev request per query.

I also tried prefetching every possible taxonomy candidate before Jev. It hit the Typesense tunnel's rate limit on a longer input, so it was not used for the quality comparison. The reported final pipeline asks Typesense only for categories Jev selected. This is a practical reason to avoid an all-spans, all-categories fan-out on submission.

## Product decision

The proposed interaction is viable as a **submit-time preview**: keep the existing Typesense one-term dropdown while typing; on Enter without a dropdown selection, call Jev once to route spans, resolve those terms through Typesense, and offer one complete configuration with its individual term matches visible. Apply unambiguous exact matches automatically; show fuzzy, ambiguous, or unsupported terms for correction and retain unresolved text as keywords. This preserves a useful default while avoiding the demonstrably wrong first-hit filters above.

Do not put Jev in the shared `parseSearchFilters` function for both submit and page load. Pass the chosen explicit slugs to the results page so one Enter causes one Jev call. Use a short request deadline with the current parser as fallback. Before making the complete configuration the automatic final choice, run a larger human-reviewed evaluation sampled from real search traffic and measure the browser-to-results path from the deployed region. On the present held-out set, 8/28 complete defaults are still wrong, and the added median wait is about half a second.

## Artifacts and reproduction

- [Query fixtures](../../scripts/experiments/jev-routing-fixtures.mjs) and [current-system answers](jev-routing-current-system-2026-09-23.json); the latter retains `gold`/`goldShape` field names from the first capture script, but those fields contain **only the baseline parser response**.
- [Human span labels](../../scripts/experiments/jev-routing-intent-labels.mjs), [validated labels](jev-routing-intent-gold-2026-09-23.json), and [actual suggestion responses for human spans](jev-routing-human-candidates-2026-09-23.ndjson).
- [Prompt and routing implementation](../../scripts/experiments/jev-routing-core.mjs), [Jev runner](../../scripts/experiments/run-jev-routing-eval.mjs), [candidate collector](../../scripts/experiments/collect-jev-routing-candidates.mjs), [span scorer](../../scripts/experiments/score-jev-routing-intent.mjs), and [configuration scorer with manual target corrections](../../scripts/experiments/score-jev-routing-config.mjs).
- [Final tuning run](jev-routing-minimal2-tune-2026-09-23.json), [held-out run](jev-routing-minimal2-holdout-2026-09-23.json), [held-out routing score](jev-routing-minimal2-holdout-intent-score-2026-09-23.json), and [held-out complete-configuration score](jev-routing-minimal2-holdout-config-score-2026-09-23.json). Earlier prompt runs and grids are adjacent files.
- [Local Next.js route template](../../scripts/experiments/filter-latency-route.ts) and [baseline capture script](../../scripts/experiments/collect-jev-routing-current-system.mjs). The temporary route and copied credentials were removed from the worktree after measurement. The route template only responds on localhost in development mode. `run-jev-routing-eval.mjs` reads a local credential file provided on its command line; no credentials are in the committed artifacts.

The experiment used the actual app's Typesense suggestion functions, not a mock or a separate ad hoc Typesense query. It does not cover server-action transport, live dropdown interaction, Vercel-region network timing, concurrent traffic, or failures under load.
