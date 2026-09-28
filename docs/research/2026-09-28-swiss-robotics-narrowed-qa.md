# Robotics classifier investigation — 2026-09-28

## Production baseline

Watchlist `c47beab8-3e96-4032-af4b-d9843bdba631`, revision 3 (`8fcbc815-1207-4d05-addc-b1f30a0ef4ee`) completed all 186 active Swiss jobs across 29 companies: 119 accepted, 67 rejected. All descriptions were accessible. Every candidate has a decision; no active job is missing. Four segments (50/50/50/36) completed through normal owner View demand, with 38 successful batches, 38 attempts, no ambiguous attempts and no outstanding reservation. Its horizon and last-caught-up timestamp both equal 15:37:05 UTC.

One of the original 187 expected postings, ABB NewGen Senior Project Manager, delisted before revision 3. It is excluded from current coverage, not treated as an unevaluated job. Its older decision remains preserved.

Independent expectations were fixed before observing revision 3. Of 176 definite active expectations, 173 agree and three are false negatives. Ten predeclared ambiguous cases remain separate; their labels were not revised to make the result pass.

| Employer / role | Posting ID | Why acceptance is expected |
|---|---|---|
| Embotech — Software Team Lead, Safety Software | `2a550ce0-df9d-442b-a9bc-818adb7ea579` | Safety and perception software for autonomous vehicles. |
| Distalmotion — Complaint Handling & PMS Specialist | `19cdab20-1f82-454a-84a8-fa03f74f9009` | Product quality, complaint investigation and customer/device support in robotic surgery. |
| Flexion Robotics — Working Student/Contractor for Physical AI Training | `7f85a2a7-8bd3-494f-a9dc-7d8ee1217974` | Demonstrations, motion-data curation and testing for humanoid training. |

## Exact input and execution checks

All 186 reconstructed normalized inputs match their production database/cache content identities exactly. None are truncated. Two raw HTML differences since the independent review consist only of transient Cloudflare scripts, which the normalizer discards. The three misses arose in separate successful five-job batches at segment offsets 0, 50 and 100, each on one attempt. They do not share a failed batch or a missing-body condition.

The exact production query is unchanged throughout the framing diagnostics; the separately reported shorter-request experiment changes only that query. The requested and validated returned model is `jev-1.13.0`; production prompt/schema/normalizer versions are `jev-job-fit-choice-v1`, `classifier-input-v1`, and `classifier-input-normalizer-v4`. The relevant cache rows were created for this revision. Source-order response mapping uses exact question keys, not response insertion order.

## Controlled diagnostic evidence

Tests used the existing client with `maxRetries: 0`, no account writes or persisted decision replacement. Local credentials are not the production credential; upstream credential-specific routing is not observable from these artifacts.

| Intervention | Flexion result | Other observations |
|---|---|---|
| Actual production, original shared-state batch | rejected | Original probability was not persisted. |
| Same exact Flexion input alone | accepted; P(accepted)=0.62 | Exact content identity matches production. |
| Original five-job membership/order; only backticked relative field references corrected | rejected; P(accepted)=0.44 | Definite positive/negative siblings remained correct. |
| Same five-job membership/order; shared state contains query, each structured instruction contains its own exact job | accepted; P(accepted)=0.80 | Both definite positive siblings stayed accepted; connector-business assistant stayed rejected. Ambiguous spontaneous application stayed rejected. |

The first solo Embotech and Distalmotion diagnostics also returned accepted, but their reconstructed metadata differed from production. They are **not valid causal parity comparisons** and were reported as such immediately. Correct canonical objects were subsequently generated; those two solo tests were not rerun.

The official [TypeSafe primitives guide](https://docs.typesafe.ai/primitives#reference-specific-fields) documents backticked paths relative to state. Correcting that notation alone did not resolve the observed miss. Its [structured instructions guide](https://docs.typesafe.ai/primitives/advanced) supports putting record-specific data alongside an individual question. Questions share state, so reducing the shared set of job documents is distinct from merely adding or removing questions.

The exact Flexion contrast supports testing question-local evidence as a generic framing improvement. It does not prove a deterministic attention failure inside the provider or establish full-set accuracy. No threshold change, job-ID exception, label override or public-query expansion is justified by this single composition. A frozen full-set replay with original batch membership/order is the implementation gate; the completed outcome is recorded below.

## Earlier provider interruption

Revision 2 paused twice with safe telemetry `provider_unavailable`, HTTP status null, two attempts and two ambiguous attempts. Current client code classifies successful-response body/JSON/schema failures as `invalid_response`, and parent cancellation as `cancelled`. These recorded failures therefore indicate fetch rejection without an HTTP response, compatible with network failure or the client's 10-second per-attempt timeout. Existing logs do not retain the cause or elapsed time, so they cannot distinguish those cases retrospectively.

Ledger `reconciled_at` receives the segment's captured time rather than completion wall time; it cannot establish request latency. Both historical uncertain charges remain intact. Normal owner retries respected the five-minute cooldown. Revision 3 subsequently completed without provider failures. No timeout, retry, budget or cooldown policy was changed.

## Initial implementation gate and preservation requirements

The frozen 186-job replay failed the initial no-new-error gate: it removed the three known misses but introduced one new definite false negative. A passing replay would justify the tested question-local structure, a prompt-version bump, and version-aware same-query configuration reuse. Normal owner View already calls configuration reconciliation before checking whether demand is covered; it can create a fresh historical revision when the prompt version changes. Old query versions, decisions, caches and budget ledger entries must remain untouched. This is not a production quality claim until the new version itself is evaluated and audited live.

## Full frozen replay outcome — initial hold

The question-local replay used all 186 exact normalized production inputs, the original 38 batch memberships and ordering, unchanged query/model/criteria, concurrency 2 and no retries. All 38 requests succeeded. Configured cost was $0.007895244. No account or production decision was changed.

It returned 121 accepted and 65 rejected. All three original false negatives were corrected, but **Mimic Senior Mechanical Engineer (Wearable Devices)** (`420ee90e-ef8b-4337-a123-9c6b35bd4983`) became a new false negative: P(accepted)=0.21, confidence=0.57. The independent acceptance remains valid: its wearable product captures hand-motion and force data to train robotic manipulation. The full body and normalized identity are unchanged.

Agreement is 175/176 definite cases, compared with 173/176 for the production baseline. Ten ambiguous cases remain separate. There were six decision flips, including two ambiguous cases. This is an improvement in this frozen sample but **does not satisfy the predeclared no-new-definite-error gate**, so no request-builder, version or configuration change was implemented.

The evidence supports request-framing sensitivity and a residual model classification error. It does not establish that the remaining miss is unavoidable, nor isolate provider variability from context sensitivity with repeated controls. Neither a new threshold fitted to this result nor more job-specific prompt examples is justified. The current model output must not be described as complete or perfectly accurate. Any further generic classifier change needs a separately specified evaluation criterion and held-out evidence; silently overriding labels or changing independent expectations would invalidate this audit.

Replay artifacts: `/tmp/robotics-revision3-question-local-full-payloads.json`, `/tmp/robotics-revision3-question-local-full-results.json`, `/tmp/robotics-revision3-question-local-full-comparison.json`.

## Accepted release decision

The user subsequently accepted the one remaining miss and explicitly authorized deployment of the question-local evidence format and the remaining article work. The 175/176 agreement is a frozen-sample result, not a guarantee for future jobs. The accepted residual case remains Mimic’s wearable-device mechanical-engineering role above; its independent expected label stays accepted.

Ship the exact evaluated request structure without changing the watchlist request, decision thresholds, retry policy, or budget behavior. Increment the classifier prompt version and ensure normal owner reconciliation creates a fresh revision for changed model, prompt, schema, or normalizer versions. Preserve historical decisions, caches and charges. Verify the deployed version through normal owner View, then check the shared feed and publication links.

This limitation belongs in this internal QA report and release evidence. It must not be added to the article copy. Final production results are recorded in the release verification section of [article PR #10159](https://github.com/colophon-group/jobseek/pull/10159) after deployment.

## Shorter request comparison

A final bounded replay simplified the public request to lifecycle language while retaining the original production builder. It evaluated the same 186 inputs in their original 38 batches, with no retries or failures. Agreement declined to 172/176 definite expectations (118 accepted, 68 rejected). It retained the Distalmotion complaint-quality miss and introduced misses in FIXPOSITION product marketing, Distalmotion clinical sales, and RIVR robotics trade management. The current public request was retained; no further variants were tried.

## Durable evaluation record

The adjacent [JSON evaluation record](2026-09-28-swiss-robotics-narrowed-qa.json) preserves all 186 input hashes, fixed expectations and rationales, production decisions, and both complete replay outcomes with batch membership and probabilities. It excludes full job descriptions, credentials and account data. The local files below retain the richer diagnostic artifacts from this run.

## Evidence index

- Full production records: `/tmp/robotics-narrowed-qa-revision3-complete.json`.
- Fixed independent comparisons: `/tmp/robotics-narrowed-qa-revision3-comparison.json`.
- All exact normalized inputs: `/tmp/robotics-revision3-all-normalized-inputs.json`.
- Original 38 ledger batches/order: `/tmp/robotics-revision3-all-batch-ledger.json`.
- Cache/version/batch parity: `/tmp/robotics-narrowed-qa-revision3-batch-parity.json`.
- Solo diagnostic limitations: `/tmp/robotics-revision3-solo-results.json`.
- Relative-path contrast: `/tmp/robotics-revision3-flexion-relative-path-result.json`.
- Question-local contrast: `/tmp/robotics-revision3-flexion-question-local-result.json`.
- Independent adjudication: `/tmp/robotics-revision3-mismatch-review.md`.
- Sanitized provider log evidence: `/tmp/robotics-narrowed-qa-provider-timing.json`.

No credentials, account identity, full copyrighted descriptions or provider error bodies are included in this report.
