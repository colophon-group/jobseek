# Private Stage A evaluation harness

This directory records the long-lived delivery plan and implements its offline
artifact gates. It does not read production, call a model, expose an API, build
an annotation UI, or claim that a dataset exists. Production extraction stays
blocked until the privacy, access, licensing, retention, and feed-ordering
owners approve it.

There is intentionally no v1 migration path. The unused format is hard-cut to
v2 rather than carried as compatibility code.

## Delivery plan and prerequisites

Before collection, record the approved purpose and retention window and the
operator allowed to run the one-off extraction. The pre-annotation artifact
contains one compact, digest-pinned extraction manifest: the repository OID;
Typesense alias, resolved versioned collection, and snapshot digest; exact
compiler and reader source/export digests; dependency-lock digest; query
template, page size, order, and time bounds; and the classifier/query
normalizer source, export, version, fallback, and truncation-policy digests.
It also contains one `hmac-sha256-v1` fingerprint of the exact compiled search
parameters for each filter. The HMAC key and raw/private filter values are
never retained.

AF-1 requests the strict whole-second interval `(cutoff-30d, cutoff)`. Because
the indexed timestamps have whole-second precision, extraction encodes this as
the equivalent effective interval `[cutoff-30d+1s, cutoff)`. Both requested
and effective bounds are pinned; a posting exactly at `cutoff-30d` is rejected
and one at `cutoff-30d+1s` is eligible. Source-snapshot identities bind the
manifest digest, the relevant compiled-query fingerprint, candidate ID,
content identity, first-seen timestamp, and source rank.

The immutable artifact graph is:

```text
detached calibration input -> approved calibration result
                            -> pre-annotation freeze
                            -> 12-card prompt feedback
                            -> annotation WIP + final critic
                            -> silver freeze
                            -> derived 32-pair audit
                            -> human feedback
                            -> gold freeze
```

Every arrow is a digest pin. The calibration-result digest binds all candidate
configurations, aggregate trial evidence, human decisions, and the selected
configuration for each role. Every later agent output references the selected
role-matching `configId`; drift is rejected. The result must cover exactly the
example IDs in the displayed, digest-pinned detached calibration artifact.

## Fixed corpus and feed provenance

- 20 distinct de-identified source filters and 25 prompt/feed bundles.
- The 15 production-shaped bundles consume 15 distinct filters once. The ten
  challenge bundles consume the other five filters exactly twice. The cohort
  filter sets are disjoint.
- Every bundle freezes eight postings, for 200 pairs total. Positions remain
  `0..7`; production uses source ranks `0..7`; challenge ranks remain strictly
  increasing. Timestamps and candidate IDs must preserve the approved source
  order. Content and source-snapshot identities detect later drift.
- Both cohorts cover every prompt locale, at least five personas, and all
  evidence conditions. Every persona appears at least twice overall.
- Target-model predictions and outputs are forbidden from every artifact and
  from the target-input loader.

Prompt review happens before annotation. Its 12 cards are derived from the
pre-annotation digest and must cover both cohorts, all four prompt locales, and
all seven personas. Silver cannot be produced unless all 12 decisions are
`keep` and the packet is explicitly approved. A revise or reject decision
requires a new pre-annotation freeze and a new review digest.

Every pair receives two blind annotations. Each annotation records label,
ambiguity, rationale code, and evidence references. Label disagreement,
ambiguity disagreement, either annotator marking ambiguity, or a policy
boundary requires a third-role adjudication linked to both annotation IDs.
Evidence condition and ambiguity remain separate: a policy boundary does not
force the adjudicator to call the item ambiguous.

## Agent fleet tuning and quality gates

Use a small detached 24–32 example calibration packet to spot-check at least
two candidate configurations per role. A candidate pins its exact model,
version, reasoning effort, and task-prompt digest. Each candidate must have one
aggregate trial with a role-specific suite, input/output digests, sample count,
blinded numeric score, and spot-check disposition/failure codes. Prompt authors
use the same eight disposable feeds; annotators use resolved human ground
truth; adjudicators use seeded conflicts; critics use seeded defects.

Human calibration decisions may be accept, reject, or unclear. Unclear items
remain recorded but are excluded from ground truth; at least 24 accept/reject
decisions are required. Only a candidate with a passing spot check can be the
single approved selection for its role. Trial detail and configuration metadata
remain machine-only and never enter the human packet. If a trial is poor,
discard the run, recalibrate, and create new digests. Do not patch labels by
hand to make quotas pass.

Prompt authors, annotators, adjudicators, and the final critic have globally
separate actor sets. The two annotators are distinct on every pair. Human
reviewer IDs may not be agent actor IDs. The final critic must approve the full
WIP before silver and its approval pins the complete reviewable WIP digest, so
it cannot be replayed after labels or evidence change.

The audit selection rule and seed are frozen before labels, but pair IDs are
derived only after the silver digest exists. Classification precedence is:

1. ambiguous or policy boundary;
2. remaining adjudicated disagreement;
3. clear agreement.

The audit includes every item in the first two categories, up to eight in each.
More than eight in either category fails the fleet-quality gate and triggers a
rerun. It then takes up to 16 cohort-stratified agreements and deterministically
backfills unused disagreement/ambiguity capacity with more agreements to reach
32. No operator chooses convenient pairs. Complete, resolved, explicitly
approved feedback for all 32 is required for gold.

## Minimal human presentation

Human feedback uses exactly three static Markdown packets:

1. `renderStageACalibrationPacket` — 24–32 detached prompt/posting examples,
   with accept/reject/unclear choices.
2. `renderStageAPromptReviewPacket` — exactly 12 pre-annotation prompt cards,
   each with generalized context, prompt, three feed titles, and
   keep/revise/reject choices.
3. `renderStageALabelAuditPacket` — exactly 32 digest-derived prompt/posting
   pairs, with accept/reject/unclear choices. Agent labels, ambiguity,
   adjudications, configurations, and provenance stay hidden.

The packets are intentionally compact and reviewable in one sitting. If useful
feedback cannot fit them, the run is rejected; the remedy is not a UI, database,
auth surface, or workflow engine.

## Private storage and use

Posting content uses production `classifier-input-v1` /
`classifier-input-normalizer-v4`; queries use the AF-1 v1 normalizer. Untrusted
text is put in collision-safe Markdown fences. JSON is canonical and digests
are domain-separated SHA-256.

`AI_FILTER_EVAL_DATA_ROOT` must be an existing absolute, process-owned,
non-symlink directory with no group/world permissions. Files are single-link,
regular, bounded canonical UTF-8 JSON and are published once without overwrite.
Inside the repository, only `apps/web/.private/ai-filter-evaluation/` is
allowed, and it is gitignored. Corpus data must never enter Git, CI artifacts,
logs, screenshots, or public datasets.

Only gold is loadable for evaluation. `loadStageATargetInputs` returns exactly
`{ pairId, query, classifierInput }`; scoring labels load separately. Reports
keep production-shaped and challenge cohorts separate and suppress small label
cells.
