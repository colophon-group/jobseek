# Search-query routing dataset and Jev prompt evolution

This index makes the query datasets, reference answers, prompt revisions, and raw experiment outputs discoverable together. The [first evaluation](jev-routing-quality-2026-09-23.md), [occupation-diverse follow-up](jev-routing-diverse-followup-2026-09-24.md), and [contextual-discard correction](jev-routing-contextual-discard-2026-09-24.md) contain the interpretation and latency findings. All inputs are **synthetic, editorial search-box examples**, not user logs. Human reference labels were authored by one annotator.

## Datasets

| Dataset | Queries | Development | Initially held out | Coverage | Source |
|---|---:|---:|---:|---|---|
| Original | 84 | 56 | 28 | Atomic, terse, verbose, mistakes, multiword, unrelated, multilingual; more developer roles | [queries](../../scripts/experiments/jev-routing-fixtures.mjs), [human labels](../../scripts/experiments/jev-routing-intent-labels.mjs) |
| Occupation diverse | 64 | 32 | 32 | Four development and four holdout examples in each of eight groups; finance, healthcare, legal, operations, sales, construction, administration, marketing, and software | [queries](../../scripts/experiments/jev-routing-diverse-fixtures.mjs), [human labels](../../scripts/experiments/jev-routing-diverse-intent-labels.mjs) |
| Contextual discard | 32 | 16 | 16 | Verbose/short queries, mistakes, unrelated and informational input, unsupported occupations, multilingual text, and place ambiguity | [gold labels](jev-routing-discard-gold-2026-09-24.json), [current parser answers](jev-routing-discard-current-system-2026-09-24.json) |

The original 28-case holdout was first opened for the `minimal2` evaluation and was then **development data** for the follow-up revisions. The occupation-diverse final 32 cases were frozen with their human labels in commit `44a286bf9` and first evaluated with the frozen `catalog5` prompt after commit `6ed895a9b`. That second holdout is now exposed too; future prompt work needs a new holdout.

Each human label identifies an exact text span and one of location, occupation, seniority, technology, remote, hybrid, onsite, or employment type. Words outside labeled spans are keywords. The new dataset has **135 labeled filter spans**, including **55 occupations**. Its holdout has 28 occupation spans and four unrelated queries. The reference configuration is formed by normalizing labeled taxonomy spans through the actual app Typesense suggestion functions, with any human-adjudicated top-hit correction recorded separately. No new holdout case required such an override.

The files that distinguish human reference from system output are:

- [Original validated reference](jev-routing-intent-gold-2026-09-23.json), [current parser output](jev-routing-current-system-2026-09-23.json), [Typesense candidates for human spans](jev-routing-human-candidates-2026-09-23.ndjson), and [human target overrides](jev-routing-legacy-gold-overrides-2026-09-24.json).
- [Occupation-diverse validated reference](jev-routing-diverse-intent-gold-2026-09-24.json), [current parser output](jev-routing-diverse-current-system-2026-09-24.json), [Typesense candidates for human spans](jev-routing-diverse-human-candidates-2026-09-24.ndjson), and [one development-only target override](jev-routing-diverse-gold-overrides-2026-09-24.json).

The `gold`/`goldShape` fields in the current parser output files retain the initial collector's names; **they contain the parser's answers, not human gold**. The human target comes from the independently written labels and candidate/override files. The new baseline was stable on all 64 queries. All 106 labeled taxonomy spans in that dataset received at least one real Typesense suggestion; candidate presence alone does not prove the candidate is correct.

## Prompt sequence

The exact policy strings, Jev request construction, and overlap selection live in [jev-routing-core.mjs](../../scripts/experiments/jev-routing-core.mjs). The catalog variants attach this [91-occupation locale-aware taxonomy snapshot](../../scripts/experiments/jev-occupation-catalog.json) to Jev state. The runner uses `jev-1.13.0`, one Choice question per one-, two-, or three-word span, then normalizes routed taxonomy spans with the app's Typesense services.

1. `natural`: broad semantic routing. On the original 56 development inputs it got 19/56 exact span sets.
2. `literal`: mimic the exact heuristic parser. It got 26/56 exact span sets but inherited literal-matching weaknesses.
3. `minimal`: shortest independent filter spans, typos and abbreviations, and unrelated-query protection. It got 43/56.
4. `minimal2`: clearer separation of technology, seniority, occupation, and work mode. It got 45/56 on the original development split; the frozen original holdout result was 20/28 exact complete configurations.
5. `broad3`: explicitly broadens career families and handles unsupported titles. It worsened new development routing to 27/32 under the eventual routing rule.
6. `catalog3`: supplies actual taxonomy occupation names, allowing multiword non-developer roles and discouraging overlap-based substitutions.
7. `catalog4`: makes separate adjacent filters, generic words, and informational questions more explicit.
8. `catalog5`: combines the catalog with a whole-query job-search decision and explicit rules for abbreviations, typos, independent spans, unsupported roles, and filler words. Its old 32/32 complete-configuration score had flawed keyword labels, as explained in the contextual-discard correction.
9. `catalog6`: adds a context-dependent `discard` choice for words that should not constrain a job search. It missed discard coverage on two informational development queries.
10. `catalog7`: adds a separate whole-query intent choice, suppressing all filter and discard choices for informational/unrelated requests. This is the frozen production policy; it scored 16/16 filter spans and 16/16 discard coverage on a new holdout, with normalization limits described in the [follow-up](jev-routing-contextual-discard-2026-09-24.md).

For a like-for-like development comparison below, every row uses probability threshold `0.6`, confidence ordered nonoverlapping spans (`longestFirst: false`), and exact suggestion match or first Typesense candidate. These scores are **development comparisons**, including the original now-exposed holdout; they must not be read as fresh holdout performance.

| Prompt | New 32 exact spans | Original 84 exact spans | New 32 exact complete configs | Original 84 exact complete configs |
|---|---:|---:|---:|---:|
| `minimal2` | 30 | 61 | 30 | 59 |
| `broad3` | 27 | Not run | 27 | Not run |
| `catalog3` | 30 | 65 | 29 | 57 |
| `catalog4` | 31 | 64 | 30 | 56 |
| `catalog5` | 31 | **67** | 30 | **61** |

`catalog5` matched **91/116 development complete configurations** across the two sets, compared with 89/116 for `minimal2` under this same rule and 86/116 for either earlier catalog prompt. The final [freeze record](jev-routing-diverse-frozen-config-2026-09-24.json) specifies model, prompt-and-catalog SHA-256, threshold, overlap rule, and normalization rule. Prompt and catalog were not edited after opening the new holdout.

On the fresh 32-query holdout, `catalog5` got **32/32 exact span sets and 32/32 exact complete configurations**. Two further runs of those same 32 inputs repeated the result. The earlier `minimal2` prompt got 30/32 on the same inputs; the actual current parser got 11/32. Repeats test response stability on those cases, not independent generalization.

## Raw runs and scoring

The development files in this directory follow `jev-routing-{variant}-legacy-development-2026-09-24.json` for the original exposed 84 and `jev-routing-diverse-{variant}-tune-2026-09-24.json` for the new development 32. `minimal2`'s original-84 file is named `jev-routing-minimal2-legacy-all-2026-09-24.json`. Adjacent `intent-grid` and `config-score` files capture scoring and routing-rule comparisons. The fresh holdout raw Jev answers and real Typesense responses are [first run](jev-routing-diverse-catalog5-holdout-2026-09-24.json), [repeat 2](jev-routing-diverse-catalog5-holdout-repeat2-2026-09-24.json), and [repeat 3](jev-routing-diverse-catalog5-holdout-repeat3-2026-09-24.json). Each has adjacent `intent-score` and `config-score` files. The [previous-prompt holdout run](jev-routing-diverse-minimal2-holdout-2026-09-24.json) is a direct comparison on the same examples.

From repository root, the following commands regenerate the validated new labels and first frozen holdout scores from checked-in data; they make no live Jev or Typesense calls:

```bash
node scripts/experiments/prepare-jev-routing-intent.mjs \
  docs/experiments/jev-routing-diverse-current-system-2026-09-24.json \
  scripts/experiments/jev-routing-diverse-intent-labels.mjs

node scripts/experiments/score-jev-routing-intent.mjs fixed \
  docs/experiments/jev-routing-diverse-catalog5-holdout-2026-09-24.json \
  0.6 false \
  docs/experiments/jev-routing-diverse-intent-gold-2026-09-24.json

node scripts/experiments/score-jev-routing-config.mjs \
  docs/experiments/jev-routing-diverse-catalog5-holdout-2026-09-24.json \
  docs/experiments/jev-routing-diverse-intent-gold-2026-09-24.json \
  docs/experiments/jev-routing-diverse-human-candidates-2026-09-24.ndjson \
  docs/experiments/jev-routing-diverse-current-system-2026-09-24.json \
  docs/experiments/jev-routing-diverse-gold-overrides-2026-09-24.json \
  0.6 false
```

The [runner](../../scripts/experiments/run-jev-routing-eval.mjs) and [current-parser collector](../../scripts/experiments/collect-jev-routing-current-system.mjs) describe the live capture inputs. They require local credentials and a development route, which are not committed. No secret appears in the checked-in dataset or outputs.
