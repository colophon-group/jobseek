# Private Stage A evaluation harness

This directory defines the offline Stage A v2 corpus contract for the AI
filter. It does not read production, call a model, expose an API, render a UI,
or claim that a dataset exists. Production collection remains blocked until
the named privacy/licensing/access/retention owners and query-ordering policy
are approved.

There is intentionally no v1 migration path. No v1 corpus was put into use, so
the unused schema is hard-cut to v2 instead of carrying compatibility code.

## Fixed corpus shape

- 20 de-identified production filter snapshots. Only a digest of the source
  filter and a generalized context are retained; user, account, watchlist,
  source URL, free-text filter values, and raw production identifiers are not.
- 25 prompt/feed bundles, eight frozen postings each: 200 prompt/posting pairs.
- Exactly 15 `production_shaped` bundles (120 pairs) and 10 `challenge` bundles
  (80 pairs). Every filter is used once and exactly five are used a second time.
- Prompts are `agent_synthetic`. Every prompt records pseudonymous author,
  agent role, model, model version, reasoning effort, and task-prompt digest.
  Cohort and persona remain separate fields.
- Every pair records evidence condition and ambiguity separately. All 200 pairs
  receive exactly two blind annotations from distinct annotators.
- Every label disagreement, ambiguous pair, and policy-boundary pair is
  adjudicated. Prompt authors, annotators, adjudicators, the approving final
  critic, and the human audit reviewer are globally separate roles.
- Target-model predictions and outputs are forbidden from every input schema.

Posting content is normalized with production `classifier-input-v1` /
`classifier-input-normalizer-v4`; soft queries use the AF-1 v1 normalizer.
Content identities and manifests use deterministic domain-separated SHA-256
digests. Validation errors expose only logical paths and fixed rule codes.

## Agent calibration and human feedback

The fleet is tuned on a disposable, detached calibration artifact before the
200-pair corpus is labelled. The artifact contains 24–32 synthetic prompt and
normalized-posting examples, never model IDs, settings, outputs, or target
predictions. It stays outside the corpus; only its approved digest is pinned in
WIP, silver, and gold files.

Human review uses three static Markdown packets, not an application:

1. `renderStageACalibrationPacket`: 24–32 detached examples with
   accept/reject/unclear checkboxes. Use this to spot-check candidate model and
   reasoning-effort combinations before choosing the fleet configuration.
2. `renderStageAPromptReviewPacket`: exactly 12 prompt cards containing the
   generalized filter context, prompt, three feed titles, and
   keep/revise/reject. It omits model/provenance data and private filter values.
3. `renderStageALabelAuditPacket`: at most 32 digest-pinned silver pairs with
   prompt and normalized posting plus accept/reject/unclear. Agent labels,
   adjudications, and provenance are hidden so the audit remains blind.

Untrusted prompt and posting text is placed in collision-safe Markdown fences;
line endings are deterministic. Feedback is recorded by stable ID. If useful
feedback cannot fit these bounded packets, the run fails and is recalibrated;
we do not build an annotation UI, database, auth surface, or workflow engine.

## Lifecycle

1. `validateStageAWip` enforces the complete shape, references, normalization,
   provenance, coverage split, annotation/adjudication rules, and role
   separation.
2. `freezeStageASilver` derives an immutable `agent_adjudicated_silver`
   manifest. Each row contains its derived silver label and annotation or
   adjudication provenance. The calibration digest must match an external pin.
3. A human precommits `ai-filter-stage-a-human-audit-policy-v2`, binding one to
   32 pair IDs to the silver digest. The policy digest is stored separately.
4. `promoteStageAGold` requires the exact silver, calibration, and policy pins;
   complete feedback for every precommitted ID; explicit approval; and an
   independent reviewer. Human corrections and confirmations are retained as
   per-row provenance in immutable `human_audited_gold`.
5. `loadStageATargetInputs` accepts gold only and returns exactly
   `{ pairId, query, classifierInput }`. `loadStageAScoringLabels` loads labels
   separately after gold exists. Reports keep production-shaped and challenge
   cohorts separate.

## Private storage

`AI_FILTER_EVAL_DATA_ROOT` must name an existing absolute, process-owned,
non-symlink directory with no group/world permissions. Files are single-link,
regular, bounded, canonical UTF-8 JSON and are published once without
overwrite. Nested paths, traversal, symlinks, duplicate JSON keys, unsafe
repository locations, and changing roots are rejected.

Inside this repository the only allowed root is
`apps/web/.private/ai-filter-evaluation/`; it is gitignored. Real corpus data
must never enter Git, CI artifacts, logs, screenshots, or public datasets.
