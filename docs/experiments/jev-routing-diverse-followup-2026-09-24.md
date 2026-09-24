# Jev filter routing: occupation-diverse follow-up

> **Score correction (2026-09-24):** The 32/32 complete-configuration metric below used a reference that kept instruction words as keywords and accepted the first Typesense hit for ambiguous places. It does not mean 32 usable searches. See the [contextual-discard follow-up](jev-routing-contextual-discard-2026-09-24.md) for corrected labels, frozen holdout results, and a production-route audit.

**Date:** 2026-09-24

**Base:** `origin/main` at `eb40e2b2a`, isolated worktree

**Scope:** prompt and experiment artifacts; production search behavior is unchanged

This follows the [first routing and latency evaluation](jev-routing-quality-2026-09-23.md). That evaluation's 84 examples leaned toward developer roles, and its frozen `minimal2` prompt matched 20/28 complete configurations on its original holdout. The original holdout had already been inspected, so this follow-up uses a new 32-query holdout.

The [dataset and prompt evolution index](jev-routing-dataset-and-prompt-evolution-2026-09-24.md) links the tuning examples, human labels, each policy revision, and raw runs.

## Result

The catalog grounded `catalog5` prompt matched **32/32 fresh holdout intent labels and 32/32 complete configurations** on its first run. Two further runs of the same 32 queries also matched 32/32 each. These are repeated measurements of **32 distinct cases**, not 96 independent holdout cases. On the same new cases, the prior `minimal2` prompt matched **30/32** configurations and the actual current parser matched **11/32**. All eight query groups had four exact configurations with `catalog5`.

The Jev plus Typesense normalization path took **392 ms median and 866 ms p95** over the 96 repeated query runs, with a **3,798 ms maximum**. These are nearest-rank percentiles of sequential requests from a local machine in Switzerland. They include the Jev API request and local Next.js route to the real Typesense suggestion functions, but exclude browser transport, search navigation, and rendering. The long tail makes an unconditional blocking Enter action risky without a deadline and fallback.

The perfect observed score is encouraging but does not establish real-user accuracy. The examples were written and labeled by one person, many occupations have an exact catalog name, and the new holdout is small. No production logs or deployed-region timings were used.

## New dataset and reference answers

I wrote **64 new realistic search-box inputs**, eight in each group: atomic, terse, verbose, misspelled, multiword, employment type, unrelated, and multilingual. The first four of each group were used for development and the remaining four for a fresh holdout. The 64 human-labeled inputs contain **135 intended filter spans**; **55 are occupation spans**. The 32 holdout inputs contain 28 occupation spans and four unrelated queries. Roles include accountant, pharmacist, recruiter, compliance officer, legal counsel, warehouse associate, construction manager, sales manager, and other non-software roles. The labels mark the exact text and filter dimension; all remaining words become keywords.

I captured the **actual current `parseSearchFilters` answers**, through the app's local Next.js route connected to its configured Typesense service, before tuning Jev. All 64 answers were stable across consecutive calls. This is a baseline, **not** the human reference. For example, `compliance officer` becomes the keyword `compliance` plus an `officer` location in the current parser, whereas the human reference is the Compliance Officer occupation.

The labeled taxonomy terms were sent through the app's actual Typesense suggestion functions: **106/106 returned a candidate, with zero lookup errors**. This measures candidate availability, not correctness. A single development example, `New York`, needed an explicit human-reference correction from the first ranked state to the intended city. The [override file](jev-routing-diverse-gold-overrides-2026-09-24.json) records it. **No fresh holdout reference needed an override**; its first ranked suggestions agreed with the intended taxonomy choice. The complete-configuration metric compares exact normalized slugs and keywords, ignoring array order.

## Prompt and holdout protocol

The candidate design asks Jev `jev-1.13.0` once per query to classify every one-, two-, and three-word text span as keyword, location, occupation, seniority, technology, work mode, or employment type. The runner looks up Jev-labeled taxonomy spans through the app's Typesense suggestion functions. A 0.6 probability threshold and confidence ordered overlap selection then choose nonoverlapping filter spans for the complete configuration; unresolved text becomes keywords. The runner sometimes looks up overlapping spans that are later discarded, so the measured normalization time includes that extra work.

I tried broad career wording and then catalog grounded prompts on the **32 new development queries and the original 84 queries**, whose former holdout was already exposed. Under the chosen 0.6/confidence ordered routing and first-suggestion normalization, `catalog5` matched **91/116** development configurations, compared with 86/116 for each of `catalog3` and `catalog4`, and 89/116 for `minimal2` scored under that same rule. These development scores guided selection; they are not fresh holdout estimates. `catalog5` includes a snapshot of the current **91 occupation taxonomy names** in the Jev state. It asks Jev to first decide whether the whole input seeks jobs, keep a complete occupation title together, separate an adjacent work mode or other filter, recognize clear abbreviations and typos, and retain unsupported titles as keywords. The chosen prompt, catalog, threshold, overlap rule, and normalization rule were committed and [fingerprinted](jev-routing-diverse-frozen-config-2026-09-24.json) **before any `catalog5` call on the fresh holdout**. None was changed after seeing holdout results.

| Method on fresh 32-query holdout | Exact intended spans | Exact complete configurations | Filter F1 |
|---|---:|---:|---:|
| Actual current parser | — | 11/32 | 0.740 |
| Prior `minimal2` prompt, prior fixed routing rule, first Typesense suggestion | 30/32 | 30/32 | 0.972 |
| Frozen `catalog5`, exact suggestion only | 32/32 | 27/32 | 0.964 |
| Frozen `catalog5`, exact match or first suggestion | **32/32** | **32/32** | **1.000** |

The prior prompt's two misses were `Find an on site compliance officer role in Munich`, where it swallowed the on-site expression into an occupation span, and `what is a compliance officer`, where it proposed an occupation for an informational question. The new prompt got both right. The current parser scored 3/4 atomic, 3/4 terse, 1/4 verbose, 0/4 misspelled, 0/4 multiword, 0/4 employment type, 2/4 unrelated, and 2/4 multilingual cases exactly. This explains why an exact occupation-name baseline alone would have hidden much of its weakness.

The strict suggestion result shows a dependency on Typesense ranking: five configurations needed the first non-exact suggestion to interpret a misspelling or translation. The exact-or-first policy happened to match every holdout reference; it should still display the chosen term and alternatives because a first hit can be wrong. The previous experiment documented examples such as `policy analyst` and `finance analyst` where top-ranked suggestions were the wrong role.

## Delay and stability

| Frozen `catalog5` holdout run | Queries | Jev median / p95 | Typesense normalization median / p95 | Combined median / p95 | Combined max |
|---|---:|---:|---:|---:|---:|
| First | 32 | 329 / 465 ms | 27 / 143 ms | 351 / 492 ms | 1,243 ms |
| Repeat 2 | 32 | 353 / 1,071 ms | 16 / 83 ms | 369 / 1,189 ms | 3,798 ms |
| Repeat 3 | 32 | 423 / 737 ms | 44 / 252 ms | 471 / 866 ms | 2,325 ms |
| All repeated runs | 96 | **357 / 737 ms** | **25 / 176 ms** | **392 / 866 ms** | **3,798 ms** |

Percentiles use nearest rank. All 96 repeated query runs produced the same correct configuration, but latency varied substantially. Jev alone took 3,715 ms on one `quality manager New York City` repeat and 2,073 ms on one `compliance officer` repeat. The frozen prompt asked a median of nine Choice questions per query and used a median of 3,479 input tokens; the catalog adds request size. The current parser's repeated local call on these 32 queries measured 53 ms median and 920 ms p95; local development and Typesense cache effects make that tail too noisy for a production SLA comparison. The separate [initial latency report](jev-routing-quality-2026-09-23.md#delay) describes the earlier focused comparison.

## Product decision and next measurement

The evidence supports a **proposed complete filter configuration on Enter** for this candidate design: keep the existing one-term Typesense dropdown while typing, ask Jev to route an entered query once, normalize the chosen spans with Typesense, and present one complete option with its individual matches visible. Preserve unresolved terms as keywords and let users correct ambiguous or fuzzy matches. Pass the selected slugs onward so page load does not invoke Jev again. Looking up only the spans that survive overlap selection could reduce normalization work; it was not measured here. A short deadline with the current parser as fallback is warranted by the observed 3.8-second outlier.

I would not infer a production 100% success rate or a production p95 from this set. Before silently applying the proposed configuration, evaluate a larger independently labeled sample of actual search traffic, including unsupported job titles, regional location ambiguities, and non-job questions. Measure Enter-to-results in the deployed region under concurrent traffic. Recheck the occupation catalog snapshot whenever the taxonomy changes.

## Reproduction and artifacts

- [Queries](../../scripts/experiments/jev-routing-diverse-fixtures.mjs), [human labels](../../scripts/experiments/jev-routing-diverse-intent-labels.mjs), [validated labels](jev-routing-diverse-intent-gold-2026-09-24.json), [current parser answers](jev-routing-diverse-current-system-2026-09-24.json), and [real Typesense candidate responses](jev-routing-diverse-human-candidates-2026-09-24.ndjson).
- [Frozen prompt and routing code](../../scripts/experiments/jev-routing-core.mjs), [occupation catalog snapshot](../../scripts/experiments/jev-occupation-catalog.json), and [freeze record](jev-routing-diverse-frozen-config-2026-09-24.json).
- [First holdout raw run](jev-routing-diverse-catalog5-holdout-2026-09-24.json), [intent score](jev-routing-diverse-catalog5-holdout-intent-score-2026-09-24.json), [configuration score](jev-routing-diverse-catalog5-holdout-config-score-2026-09-24.json), plus adjacent `repeat2` and `repeat3` raw runs and scores.
- [Prior-prompt holdout comparison](jev-routing-diverse-minimal2-holdout-config-score-2026-09-24.json). All development prompt runs and scores are adjacent in this directory.
- [Baseline capture](../../scripts/experiments/collect-jev-routing-current-system.mjs), [human-label validator](../../scripts/experiments/prepare-jev-routing-intent.mjs), [Jev runner](../../scripts/experiments/run-jev-routing-eval.mjs), [intent scorer](../../scripts/experiments/score-jev-routing-intent.mjs), and [configuration scorer](../../scripts/experiments/score-jev-routing-config.mjs). The temporary local route and copied local credential file are removed after measurement. No credential is present in these artifacts.
