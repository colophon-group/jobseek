# AI-filter evaluation data delivery plan

Status: approved plan for the first evaluation-data delivery. Documentation,
contract/tooling changes, synthetic tests, and non-production dry runs are
authorized. A production read remains blocked on the approvals below.

This document turns the broader evaluation direction in
[24 - Subscriber AI Filter](24-ai-filter.md#evaluation-plan) into a bounded
first delivery. It does not authorize a production read, a provider call, or
publication of evaluation content. The 1,000-pair corpus in the broader plan
is a possible later expansion; the approved first delivery is exactly 200
pairs.

## Dataset contract

The unit of work is a private **feed-prompt bundle**: one de-identified
production-derived structured-filter snapshot, one synthetic soft prompt, and
eight ordered candidate postings. The structured filters establish candidate
provenance and remain evaluation metadata; they are not classifier input.

The v1 corpus contains:

- 20 distinct canonical structured filters;
- 25 feed-prompt bundles, with five additional uses of already represented
  filters allowing prompt variation;
- exactly eight posting pairs per bundle; and
- exactly 200 `(soft prompt, posting)` pairs (`25 * 8`).

Every filter, bundle, posting, and pair receives an opaque stable ID. Pair IDs
are derived from the frozen dataset version, bundle ID, and position, not from
account IDs or raw content. A posting may occur under more than one prompt, but
each `(bundle ID, position)` is one distinct pair. Once annotation begins,
replacement or reordering requires a new dataset version.

Keep two cohorts separate in manifests, review packets, and reports:

- **Production-shaped** bundles preserve a real structured-filter shape,
  observed candidate base rate, and the first eight eligible candidates in the
  frozen production order. There is no label-aware selection.
- **Challenge** bundles deliberately exercise near misses, missing or
  contradictory evidence, multilingual text, prompt injection, and other
  hard cases. They must retain source-filter and original-rank provenance, but
  they are not included when reporting the production-shaped acceptance base
  rate.

The cohort allocation and challenge selection rules are frozen before any
labels are visible. Report aggregate and cohort metrics separately; never
blend challenge cases into a claim about production prevalence.

## Sampling procedure

Sampling is a two-pass operation so the final selector is informed by the
population without repeatedly exposing production rows:

1. Run the aggregate-only census once. It reports fixed, small-cell-suppressed
   counts for company scope, filter dimensions, keyword count, and filters in
   use. It returns no filter values or identifiers.
2. Freeze a 20-slot allocation across the observed broad/narrow, company-scope,
   keyword/no-keyword, and simple/compound-filter shapes. Do not create tiny
   strata merely to satisfy a matrix; collapse unsupported slots before the
   row-level extraction.
3. In one approved extraction transaction, consider only active configurations
   that can produce an eight-posting feed at the fixed UTC cutoff. Select no
   more than one configuration per account and one per canonical filter
   fingerprint. Selection inside a slot uses a recorded deterministic keyed
   rank; account, watchlist, and raw-filter identifiers do not leave the
   extraction boundary.
4. Materialize 15 distinct filters as 15 production-shaped bundles. Each keeps
   the first eight eligible candidates in the pinned total order.
5. Materialize the other five distinct filters as two challenge bundles each,
   yielding ten challenge bundles. The two prompts may differ, but their
   candidate selection rules are predeclared and label-blind. Challenge
   heuristics may target missing evidence, close lexical matches, multilingual
   content, contradictions, and prompt-injection text; they may not inspect a
   fit label or target-model prediction.

This gives `15 * 8 = 120` production-shaped pairs and `5 * 2 * 8 = 80`
challenge pairs while retaining exactly 20 distinct source-filter
fingerprints. If the census cannot support that allocation without a privacy
exception or contrived strata, stop and revise the allocation in #8325 rather
than quietly substituting rows.

Raw keyword text, when present, may be used only inside the approved boundary
to reproduce the source candidate feed. Prompt authors receive the generalized
filter shape and frozen feed, never that text. The private manifest records
only keyword presence/count plus an approved non-reversible fingerprint if one
is required for replay validation.

## Required fidelity pins

The extraction manifest must make the sample replayable without silently
following moving code or data:

- repository commit OID, schema version, UTC extraction cutoff, inclusive and
  exclusive window bounds, Typesense collection/alias target, and canonical
  fingerprints of all 20 de-identified filters;
- exact normalizer file/function, source digest, dependency-lock digest,
  locale fallback order, included payload fields, Unicode/line-ending rules,
  HTML-to-text behavior, and 12,000-character text-boundary truncation rule;
- exact candidate compiler/reader file and function, filter string, search
  parameters, page size, and candidate-order policy; and
- ordered posting IDs before and after any challenge selection, plus a digest
  of each final normalized classifier payload.

The AF-1 total order is `postingFirstSeenAt DESC, candidateId ASC`. Current
`main` does not yet satisfy it: `order: "newest"` compiles only Typesense
`sort_by: "first_seen_at:desc"` through
`buildWatchlistCandidateSearchParams` in
`apps/web/src/lib/search/watchlist-candidate-query.ts`, so equal-time selection
can inherit engine insertion/batch rank. AF-8 must add and backfill a compatible
stable sortable key and prove page-boundary behavior before the row-level
production extraction. See the
[#8333 prerequisite record](https://github.com/colophon-group/jobseek/issues/8333#issuecomment-5634953859).
The extraction must still persist the returned ID sequence and all ranks;
evaluation replay uses that frozen sequence instead of rerunning a live query.
At whole-second index precision the eligible retention interval is
`(cutoff - 30 days, cutoff)`, compiled through the canonical half-open reader
as `[cutoff - 30 days + 1 second, cutoff)`. A posting exactly at the older
retention boundary is already expired and is not sampled.

The classifier normalizer is not implicitly the crawler's storage HTML
normalizer. The tooling PR must designate and test one exact classifier
normalizer. Sampling is blocked until that artifact is pinned, and a later
normalizer change creates a new dataset version rather than mutating v1.

## Approval boundary

No agent or operator may read production for this work until all of the
following are recorded explicitly in the governing issue:

- a privacy decision approving the precise source tables/objects and fields,
  de-identification and redaction rules, access list, private storage,
  retention, deletion, and incident handling;
- a licensing decision approving private evaluation use of the job text and
  required provenance;
- a read-only access plan naming the approver, operator, credentials, exact
  query/tool, time window, row and byte ceilings, audit logging, and expiry;
  and
- approval of the synthetic-prompt policy, protected-characteristic boundary,
  annotation guide, and allowed model processing terms.

Production access is one bounded, logged, read-only extraction. It may not
write back, update timestamps, create durable production objects, or reuse
private account identifiers or raw user prompts. Remove account, user,
watchlist, email, URL-token, and other linkable identifiers before material
leaves the approved extraction boundary. Raw extracts, prompts, descriptions,
labels, and review packets stay in approved private storage and are never
committed or uploaded to the existing public Hugging Face dataset.

This plan originally conflicted with the acceptance policy in
[#8325](https://github.com/colophon-group/jobseek/issues/8325) and Draft
[#8498](https://github.com/colophon-group/jobseek/pull/8498), which forbade
agent-created prompts and/or labels. The human-approved scope correction is now
recorded in [#8325](https://github.com/colophon-group/jobseek/issues/8325#issuecomment-5634034857)
and [#8467](https://github.com/colophon-group/jobseek/issues/8467#issuecomment-5634048323).
The Draft harness implementation and its documentation must still be reconciled
with that decision before calibration, prompt creation, annotation, or any
production read.

### Aggregate census runbook

The first approved production action is the aggregate census only. Before an
operator runs it, the #8325 approval record must contain the approval ID, unique
run ID, exact TLS endpoint/database/SELECT-only role, operator, output location,
and confirmation that this is the single permitted snapshot. Reusing a run ID
or taking a second snapshot requires a new approval; the read-only tool cannot
truthfully enforce one-shot use with durable state.

From `apps/web`, with a private `umask 077` shell and the output path outside the
repository, set:

- `AI_FILTER_EVAL_DATABASE_URL`;
- `AI_FILTER_EVAL_APPROVAL_ID` and `AI_FILTER_EVAL_RUN_ID`;
- `AI_FILTER_EVAL_APPROVED_HOST` (including the explicit port when present);
- `AI_FILTER_EVAL_APPROVED_DATABASE`; and
- `AI_FILTER_EVAL_APPROVED_ROLE`.

Then run exactly:

```sh
pnpm ai-filter-eval:census -- --confirm-read-only-production-census > "$APPROVED_PRIVATE_CENSUS_PATH"
```

The collector requires certificate-verified TLS (`sslmode=verify-full`),
verifies the expected database and role, verifies both default and current
read-only state, checks that the role lacks write privileges on the two source
tables, and emits only fixed aggregate cells with complementary suppression.
Record the command exit status and output digest in #8325 without attaching the
private census. Do not rerun automatically after a timeout or partial failure;
the access owner decides whether a replacement run is allowed.

The census is deliberately marked `aggregate_shape_approximation_v1` because
PostgreSQL and JavaScript differ on unusual Unicode trim/length/case behavior.
It is sufficient for coarse slot planning, not admission into the dataset. The
later bounded extractor must run each selected configuration through
`normalizeWatchlistFiltersForRead` before fingerprinting or candidate reads;
invalid or normalized-duplicate rows are replaced inside the same preapproved
slot policy.

## Blind agent calibration

Before v1 annotation, compare `gpt-5.6-sol` and `gpt-5.6-terra` across the
predeclared reasoning-effort settings on the same disposable set of 24-32
human-reviewed pairs. The set is disjoint from the 200-pair corpus and is not
reused in model benchmarking.

Human labels and model/configuration identities remain hidden during each run.
Freeze prompts and decoding/output rules first, randomize presentation order,
run configurations in isolated contexts, and score only after outputs are
locked. Record pair agreement with the human, accept/reject errors, ambiguity,
schema validity, run-to-run consistency, latency, and token use. The
calibration manifest must enumerate the tested effort values—no model default
may stand in for a pin—and record the selected labeller and adjudicator
configurations before the 200 pairs are opened for annotation.

Calibration data is destroyed or quarantined as non-benchmark material after
the selection record is complete. It must not inflate v1 quality results.

Tune each fleet role independently rather than choosing one global model:

- **prompt generator:** compare candidate model/effort settings on the same
  eight disposable feeds. The orchestrator blind-scores constraint fidelity,
  plausibility, persona distinctness, and policy compliance; the later
  12-card human prompt-sanity pass remains the release check;
- **primary labeller:** use the 24-32 locked human decisions for binary and
  ambiguity agreement, repeat consistency, false-accept/false-reject balance,
  schema validity, latency, and token cost;
- **adjudicator:** use prebuilt conflicting annotation records over the same
  disposable pairs and score resolution against the locked human decision; and
- **final critic:** use synthetic manifests with seeded provenance, leakage,
  count, blindness, and cohort-reporting defects and measure defect recall plus
  false alarms.

For each role, the orchestrator spot-checks raw outputs before scores are
unblinded, records observed failure modes, and may refine the task prompt only
on disposable examples. After any prompt change, rerun every compared
configuration on the same clean calibration inputs. Freeze the selected model,
version, reasoning effort, and task-prompt digest per role before final prompt
generation or annotation begins.

## Annotation and adjudication

All 200 pairs are independently labelled twice by agents. A label is binary
accept/reject plus clear/ambiguous, with an optional evidence span or concise
QA note. The second labeller cannot see the first label, its rationale, model
identity, or run metadata. Completeness is `200 * 2 = 400` locked primary
labels; partial double-labelling is not acceptable.

Every disagreement in either decision or ambiguity state is adjudicated by a
separate agent pass after both primary labels are locked. The adjudication
record names the stable pair ID, both label record IDs, final decision, final
ambiguity state, and concise rationale. Preserve rather than overwrite both
primary labels. Agreement does not skip the later human audit.

Agent-only output is **silver**, even after agent adjudication. It remains
silver until a bounded human audit, with its size and stratified selection rule
precommitted before agent labels are revealed, is complete and an authorized
human records an explicit accept/revise/reject decision. The audit covers both
cohorts and samples agreements as well as disagreements. Audit findings are
resolved by stable ID; no silent bulk relabelling is allowed. Dataset-level
approval does not imply that every row was human-labelled, so every row keeps
its `agent_agreed`, `agent_adjudicated`, or `human_reviewed` provenance.

Because the existing Draft harness has no real corpus, reconcile it as a hard
schema-v2 cut rather than adding a v1 migration layer. Preserve its strict
validation, canonical digests, private-path controls, immutable publication,
and nonleaking errors, but replace the obsolete human-authorship model with
explicit bundle/pair, cohort, persona, evidence, ambiguity, agent-provenance,
silver, audit, and gold records. Reject v1 artifacts instead of converting
them.

Target-model predictions are not a field in WIP, silver, or gold artifacts.
The AF-3 prediction loader exposes only stable pair ID, soft query, and
classifier input; a separate scorer joins locked predictions to labels after
the run. This prevents the target-model call path from receiving gold labels
or annotation metadata.

## Human review packets

Use three small, static, private Markdown packets. Do not build a custom UI,
database, authentication layer, or review service for this delivery. They are
separate because calibration must finish before final prompt generation and
annotation, while the final audit must stay blind until silver labels lock.

1. **Agent-calibration packet:** show 24-32 disposable pair cards drawn from the
   separately approved calibration set. Each card contains a stable calibration
   ID, synthetic prompt, and normalized posting payload. The reviewer marks
   `accept`, `reject`, or `unclear`; model/configuration identities and outputs
   remain hidden until all human labels lock. These examples and labels never
   enter the final 200-pair corpus.
2. **Prompt sanity packet:** before final labels exist, show 12 representative bundle
   cards spanning all locales and major persona classes. Each card contains its
   stable bundle ID, generalized hard-filter context, proposed prompt, and
   three posting titles. The reviewer marks `keep`, `revise`, or `reject`.
3. **Label audit packet:** after agent adjudication, show at most 32 pair cards:
   up to 16 stratified agreements, up to eight adjudicated disagreements, and
   up to eight ambiguous or policy-boundary cases. Backfill unused slots with
   stratified agreements. Each card contains its stable pair ID, cohort,
   prompt, and normalized posting payload. The first pass remains blind to
   agent labels and configuration identities; comparison is shown only after
   the human decision is locked.

Each packet cover asks for only the decisions needed at that gate. Calibration
and label-audit passes use `accept`, `reject`, or `unclear`; the prompt pass uses
`keep`, `revise`, or `reject`; every card has only an optional short note. The
maximum planned human surface is 32 calibration cards, 12 prompt cards, and 32
final-audit cards: 76 bounded decisions across three checkpoints. No dashboard,
queue, account, or workflow UI is part of v1.

If the disagreement or policy-boundary volume cannot fit this bounded packet,
do not turn the packet into a larger interface. Fail the fleet-quality gate,
repair the rubric or agent routing, and rerun the affected slice.

The reviewer records feedback in Markdown keyed only by stable ID, for example:

```markdown
| pair_id | human_decision | ambiguity | disposition | note |
|---|---|---|---|---|
| aife-v1-b07-p03 | accept | clear | agree | Required evidence is explicit. |
```

For the first delivery, the orchestrator checks the returned stable IDs and
echoes its interpretation for confirmation; no separate feedback application
or general-purpose form engine is required. Store the packet and feedback
beside the private dataset, not in Git or production. Preserve packet and
feedback digests in the non-sensitive release manifest.

## Deliverables

Repository-safe deliverables:

- this execution plan and a versioned annotation/schema contract;
- deterministic sampler, normalizer, manifest, and small Markdown-packet
  generator with synthetic fixtures only;
- validators for counts, stable IDs, cohort separation, independent labels,
  and adjudication completeness; and
- a sanitized result report containing aggregate/cohort metrics, provenance,
  version pins, artifact digests, audit disposition, and no private content.

Private deliverables:

- the approved de-identified filter snapshot and extraction log;
- the 25 frozen feed-prompt bundles and 200 normalized pairs;
- the disposable calibration set and locked calibration result;
- 400 primary labels, disagreement/adjudication records, and provenance; and
- the human review packet, feedback-by-ID file, remediation record, and signed
  release decision.

## Quality gates

The delivery fails closed unless all gates pass:

1. **Policy and authority:** the #8325 decision record and #8498 implementation
   agree; privacy, licensing, model-processing, storage, and exact read-only
   access are approved before production access.
2. **Reproducibility:** normalizer, candidate compiler/order, window, source
   revision, dependencies, ordered IDs, and normalized payload digests are
   pinned. Rerunning local fixtures is deterministic.
3. **Shape:** exactly 20 distinct filter fingerprints, 25 bundles, eight pairs
   per bundle, 200 stable unique pair IDs, and separately identified
   production-shaped and challenge cohorts.
4. **Privacy:** automated and manual checks find no prohibited identifiers,
   secrets, raw private prompts, or unapproved fields; private artifacts never
   enter Git or the public dataset.
5. **Calibration:** the blind 24-32-pair comparison is complete and selected
   agent/configuration pins are frozen before corpus labelling.
6. **Labelling:** exactly 400 independent primary labels exist; all
   disagreements are adjudicated with original labels retained.
7. **Human audit:** the precommitted bounded packet is complete, feedback IDs
   validate, findings are resolved, and the human release decision is
   explicit. Until then the dataset is silver.
8. **Release:** the private manifest and sanitized report agree on counts and
   digests, production-shaped and challenge results are reported separately,
   and no calibration pair appears in the 200-pair benchmark.

## Issue and PR sequence

1. Merge the documentation-only plan PR. It grants no data-access authority.
2. Reconcile #8498 with the approved #8325 synthetic-prompt, double-agent-label,
   agent-adjudication, and human-audit workflow; record the remaining
   production-read approvals on #8325.
3. Open a tooling PR containing only schemas, deterministic extraction and
   validation code, the exact normalizer/order pins, packet generation, and
   synthetic tests. Review it without production credentials or data.
4. After that PR passes and the named production-read approval is recorded,
   perform the single bounded read-only extraction into approved private
   storage and freeze the 25-bundle manifest.
5. Run and lock blind calibration, then run 400 independent labels and
   adjudicate every disagreement. Validate the private corpus as silver.
6. Generate the static Markdown audit packet, collect human feedback by stable
   ID, resolve findings, and record the release decision.
7. Open a results PR with only the sanitized report, schema/tooling fixes, and
   non-sensitive version/digest metadata. Do not attach the private corpus.
8. Only after all gates close may #8326 consume the approved v1 corpus for the
   classifier benchmark. Expansion toward 1,000 pairs is a separately approved
   follow-up, not an automatic continuation.
