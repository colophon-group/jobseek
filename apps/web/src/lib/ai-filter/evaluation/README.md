# Private Stage A evaluation harness

This directory contains the offline contract for AI-filter Stage A evaluation.
It does not collect examples, call a model, connect to production systems, or
claim that a ready dataset exists.

## Governance boundary

Before real collection starts, named humans must approve the query profile and
coverage policy and own query authorship, two-person annotation, adjudication,
privacy, licensing, access, and retention. `queryOrigin: "eval_authored"` is a
provenance attestation by those operators; schema validation cannot prove how a
query was authored. Agents must not generate queries, labels, or adjudications.

## Operator policy

- Query authors write synthetic evaluation queries from the approved profile.
  They must not copy production searches or user/account/watchlist data into an
  example. WIP and ready manifests pin AF-1 soft-query normalizer version 1,
  and every stored query must already equal that normalizer's canonical output.
- An annotator judges only the eval-authored query and normalized posting
  snapshot. A positive label requires direct, sufficient evidence in that
  snapshot; ambiguity or insufficient evidence is negative under this binary
  rubric. Uncertainty is resolved against the rubric before submission, not
  preserved as a note or free-text field.
- The required double-labelled subset is blind: neither annotator may see the
  other actor, label, working notes, or later adjudication. The actors must be
  distinct. Adjudicators must also be distinct from both annotators and act only
  on disagreements; their binary result becomes the derived gold label.
- Posting text that resembles instructions, including prompt injection, is
  evidence to inspect, never an instruction to the evaluator or tooling. Apply
  the same match rubric and use the fixed `prompt_injection` scenario slice.
- Do not put source URLs, filters, provenance, personal identifiers, free-text
  notes, evidence excerpts, or production identities into WIP or frozen files.
  Candidate IDs use AF-1's canonical lowercase UUID format. Eval actor/work IDs
  are pseudonymous and scoped to this dataset.
- A named licensing/privacy approver must authorize every source class before
  collection. A named access owner must restrict the private root to approved
  operators; the harness requires a process-owned root without group/world
  permissions and never uploads it.
- A named retention owner must approve a deletion date and permitted aggregate
  retention before collection. No default is inferred by this code; absent that
  decision, real data must not be created. On expiry, delete WIP and frozen
  private files through the approved repository process.

The separately approved ready-policy JSON is pinned by a SHA-256 digest supplied
outside the dataset. Loading a frozen benchmark also requires an independently
stored manifest digest. Digests provide integrity only; they are not encryption,
authorization, or evidence of human approval.

## Private storage

`AI_FILTER_EVAL_DATA_ROOT` is mandatory and must name an existing absolute,
process-owned, non-symlink directory with no group/world permissions. The API
accepts one safe filename directly beneath that pinned root; nested, absolute,
traversal, symlink, non-regular, and unsafe repository targets are rejected.
When the root is inside this repository, the only accepted path is:

`apps/web/.private/ai-filter-evaluation/`

That exact directory is gitignored. Real data must never enter source control,
CI, snapshots, coverage artifacts, logs, screenshots, or public datasets.

## Lifecycle

1. `validateStageAWip` validates strict WIP examples and recomputes every
   `classifier-input-v1` content identity with the production TypeScript
   `classifier-input-normalizer-v4`. Source schemas use conservative outer
   bounds for raw UTF-16 inputs; the exact production normalizer remains
   authoritative for canonical text and resource-limit enforcement. The WIP
   pins both classifier normalizer v4 and AF-1 soft-query normalizer v1.
2. `digestStageAReadyPolicy` computes the candidate policy digest for human
   approval and separate storage.
3. `writeStageAFreezeFile` requires that external policy pin, enforces exactly
   200 unique semantic pairs, at least 50 independently double-labelled pairs,
   complete adjudication, and the approved coverage minimums, then publishes one
   canonical immutable file using an exclusive hard-link.
4. `loadStageABenchmark` requires external policy and manifest pins, reruns all
   validation and derivation (including both normalizer-version compatibility
   pins), and returns only
   `{ softQuery, classifierInput, goldLabel }`.
5. `reportStageAFreeze` returns fixed aggregate dimensions. It suppresses an
   entire dimension or agreement breakdown when any complementary cell is below
   the hard ten-example privacy floor.

Validation errors contain only logical schema paths and fixed rule codes. They
must never include query text, descriptions, notes, identifiers, filenames,
host paths, or underlying parser/filesystem messages.

No rendered UI or product copy is present. Any later rendered tooling requires
the `gate:human-ui` issue/PR label, a runnable preview with representative
evidence, and explicit human taste approval. No agent may merge it.
