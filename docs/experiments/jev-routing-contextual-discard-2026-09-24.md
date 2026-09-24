# Jev search-query routing with contextual discard

**Date:** 2026-09-24. **Base:** `origin/main` at `eb40e2b2a`, isolated worktree. Inputs are synthetic editorial search-box examples, labeled by one person. These results are not a production accuracy estimate.

## Correction to the earlier score

The [occupation-diverse follow-up](jev-routing-diverse-followup-2026-09-24.md) reported `catalog5` as 32/32 exact complete configurations. Its reference builder treated every unselected word as a title keyword, including instructions such as “Find”, “role”, and “in”. That score therefore rewarded unusable keyword residue on verbose searches. It also accepted the first Typesense candidate for ambiguous places. The raw run remains useful for filter-span routing, but **32/32 should not be interpreted as 32 usable searches**. This follow-up labels meaningful keywords and context-dependent discard explicitly, and the production normalizer keeps ambiguous candidates unselected.

## Frozen data and prompt progression

The [new gold set](jev-routing-discard-gold-2026-09-24.json) has 32 cases: 16 development and 16 holdout, frozen in commit `847505f89` before Jev was run on the holdout. It includes verbose and shortened searches, spelling errors, unsupported titles, multilingual text, informational requests, place ambiguity, and jobs in healthcare, finance, legal, marketing, operations, logistics, and software. Each case labels intended filter spans, meaningful residual keywords, and word coverage to discard. The [current parser answers](jev-routing-discard-current-system-2026-09-24.json) were captured through the local Next.js parser against real Typesense; their `answer` fields are **baseline output, not human gold**. On the new 16 holdout cases, the current parser had the exact intended keyword list on only **1/16**. It routinely kept instruction words as title keywords and classified two informational queries as filters.

`catalog6` added a `discard` category to the 91-occupation catalog prompt. On the 16 development cases it got 16/16 filter-span sets and 14/16 discard coverage; its two discard misses were informational questions. `catalog7` added a separate whole-query `jobSearch`/`other` decision and uses that decision to suppress all filters and discard for informational input. The [prompt, model, threshold, and overlap rule were frozen](jev-routing-discard-catalog7-freeze-2026-09-24.json) in commit `310639463` before the new holdout was opened. The deployed policy is generated from the same [prompt evolution source](../../scripts/experiments/jev-routing-core.mjs) by [sync-jev-search-policy.mjs](../../scripts/experiments/sync-jev-search-policy.mjs). There is **no fixed stopword list**; Jev decides whether a word is disposable in the context of the full query.

| Evaluation | Filter spans exactly match gold | Discarded word coverage exactly matches gold |
|---|---:|---:|
| `catalog6`, 16 development cases | 16/16 | 14/16 |
| Frozen `catalog7`, 16 development cases | 16/16 | 16/16 |
| Frozen `catalog7`, **16 new holdout cases** | **16/16** | **16/16** |
| Frozen `catalog7`, 64 already-exposed diverse cases | 63/64 | Not labeled |

The one exposed-set filter miss is `salesforce sales engineer`: Jev chose Sales Engineer but left Salesforce as a keyword instead of a technology. This is a known limitation, not a reason to revise the frozen holdout score. [Development](jev-routing-discard-catalog7-tune-2026-09-24.json), [new holdout](jev-routing-discard-catalog7-holdout-2026-09-24.json), and [exposed regression](jev-routing-diverse-catalog7-exposed-2026-09-24.json) contain per-query selections and delays.

## Complete configuration audit

I then ran the [production-style route](../../apps/web/app/api/search/query-intent/route.ts) on all 16 new holdout queries with the real Jev and Typesense services. The [raw route audit](jev-routing-discard-catalog7-route-audit-2026-09-24.json) records the final keywords, taxonomy slugs, unresolved terms, and timing. This is a **post-hoc normalization audit**, not a second blind complete-configuration score; the frozen labels specify intended spans and keywords, but not every disambiguated taxonomy slug.

- **10/16** produced the intended usable configuration automatically, including verbose `Show me entry level warehouse associate jobs in Basel`, unsupported `nurse Zurich remote`, non-job questions, and `jobs at Google for legal counsel` with Google retained as meaningful text.
- **5/16** routed the right place span, but Typesense returned plausible multiple places: London, Amsterdam, Dublin, New York, or Genève. The route leaves these words as keywords and offers location choices in the UI. Automatically taking the first Typesense hit would create a wrong or unjustified filter.
- **1/16** routed the abbreviation `acctnt` to occupation, but Typesense could not safely resolve it to Accountant. The route retained `acctnt` as a keyword, preserving the search rather than inventing a different occupation. `pharmacst` did resolve to Pharmacist.

The production policy first selects nonoverlapping spans, then normalizes only those taxonomy spans. It deduplicates repeated spans and caps Typesense normalizations at eight per query. Jev may discard instructions while leaving unsupported titles and company names as keywords. The separate whole-query decision prevents an informational question such as `salary for accountant in Zurich` from silently becoming an Accountant + Zurich job search.

## Delay and interaction cost

| Local sequential run | Cases | Median | P95 | Maximum |
|---|---:|---:|---:|---:|
| Jev call, frozen holdout | 16 | 353 ms | 1,238 ms | 1,238 ms |
| Complete local route, same 16 cases | 16 | 422 ms | 1,573 ms | 1,573 ms |

These are nearest-rank percentiles from local Switzerland requests, not deployed-region or concurrent-traffic measurements. The route result includes Jev and Typesense normalization; the wall time may include local development compilation and loopback transport. A 900 ms idle debounce puts the full proposal after the fast term suggestions. Enter reuses the pending proposal, with a 1.8 s browser deadline and a safe current-parser fallback. The Jev route has a 2.2 s upstream deadline. The long tail warrants that bounded fallback.

The frozen holdout used a median of **6,257 Jev input tokens per query** (p95 **9,399**), because the occupation catalog and category criteria accompany the span questions. This is a meaningful provider cost and request-size weakness. The client makes at most one Jev call per unchanged query, and the server caps spans/questions; a later prompt-size reduction needs a new untouched evaluation set before replacing this frozen policy.

The shared desktop header, Explore mobile toolbar, and company-page header were exercised in a local browser. A pasted four-part query showed separate Accountant, Entry Level, Zurich, and Hybrid suggestions before its complete proposal; Enter reached `loc=zurich&occ=accountant&sen=entry&wm=hybrid&qmode=literal`. A verbose compliance-officer search dropped its instruction words and reached on-site, occupation, and Munich filters. Company-page Enter stayed on the company route. Local screenshots and test evidence are summarized in the PR; browser delays from a development server are not a production responsiveness SLA.

For one pasted four-part query on the local development server, the desktop Accountant term appeared at 905 ms and the full proposal at 2,229 ms; the mobile proposal appeared at 1,956 ms. In a separate local run, Enter-to-updated URL took 1,254 ms on desktop and 1,790 ms on mobile. Each sent one Jev request. The 900 ms idle window is included in proposal time. These samples are too small and environment-dependent to set a deployed p95; they show why the term list must remain usable while the complete proposal is pending.

Review screenshots: [desktop light](jev-routing-ui/desktop-light.png), [mobile dark](jev-routing-ui/mobile-dark.png), [ambiguous place choices](jev-routing-ui/ambiguous-light.png), and [company mobile dark](jev-routing-ui/company-mobile-dark.png).

The shared `SearchBar` also appears in the desktop app header outside Explore, and the mobile search toolbar is reused on Explore and company pages. Watchlist editing uses separate filter inputs and the company modal searches company entities; those are distinct interactions and retain their current parsers. The public search and watchlist REST routes also retain their current request contract. The new Jev call is made only from the shared free-text `SearchBar` while its feature flag is on.

## Rollout interpretation

The new endpoint is disabled by default and bounded by query size, span count, client and upstream deadlines, and Redis rate limits. The first term uses the existing short typeahead debounce. Earlier terms use one batched Typesense request after an extra pause, only for inputs of at most seven words. Long sentences rely on the active term first and Jev after idle. The route runs on Node.js/Fluid; upstream wait adds provisioned-memory time and one invocation, while its active CPU cost must be measured after deployment. The feature should stay behind flags through UI review and canary. A 12-hour clean post-deployment observation should compare active CPU, provisioned memory, invocations, latency, errors, and correction/timeout rates against the existing budget. No production CPU or memory claim can be made from this local experiment.

## Reproduction

The [discard evaluator](../../scripts/experiments/evaluate-jev-discard.mjs) takes the checked-in gold JSON, local Jev token environment file, variant, and split. The [route evaluator](../../scripts/experiments/evaluate-jev-production-route.mjs) calls a locally running flagged web app while respecting its rate limit. The [legacy collector](../../scripts/experiments/collect-jev-discard-current.mjs) needs a temporary local route exposing `parseSearchFilters`; that route was removed after capture. Tokens and local environment files are not committed.
