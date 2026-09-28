# Narrowed question-local job evidence — 2026-09-28

## Problem and change

The Jev request previously put every job in shared state and asked each Choice
question to select its job by path. A job's decision could therefore see unrelated
batchmate descriptions even though its persisted cache identity is per-job.
The evaluated replacement keeps only the unchanged user query in shared state
and puts the corresponding normalized job inside that question's structured
`instructions: { question, job }`. The generic instruction still treats all job
fields as untrusted evidence and requires a clear match; choice criteria, model,
thresholds, retries, timeouts, batch size and budgets are unchanged.

TypeSafe documents [relative field references](https://docs.typesafe.ai/primitives#reference-specific-fields)
and [structured instructions](https://docs.typesafe.ai/primitives/advanced).
This is an evidence-scope correction, not a claim about deterministic model
behavior or proof of the provider's internal cause.

## Frozen evaluation

The production Swiss robotics query revision 3 completed 186 active jobs across
29 employers: 119 accepted, 67 rejected, 38 calls and no provider failures.
All 186 full descriptions were accessible and reconstructed normalized content
identities exactly matched the persisted production cache identities. Independent
review established 176 definite expectations and 10 genuinely ambiguous cases;
one additional historical job had been delisted and was excluded.

The original request agreed on 173/176 definite cases. Its three definite false
negatives were Embotech Safety Software Lead, Distalmotion Complaint Handling /
Post-Market Surveillance, and Flexion Physical AI Training.

Controlled diagnostics held query, model, normalized content and batch membership
constant. Changing only shared-state paths to documented relative backticks did
not fix the Flexion case (accept probability 0.44). The exact Flexion input alone
accepted (0.62). Putting evidence in each question accepted it (0.80), with all
four original batchmates' decisions unchanged. Two other early solo diagnostics
had metadata differences and are excluded as causal evidence.

The full question-local replay preserved all 186 content identities and the exact
membership/order of all 38 production batches. With concurrency 2 and no retries,
it completed 38 calls without errors: 121 accepted and 65 rejected, agreeing on
175/176 definite cases. All three original misses were corrected; six labels
changed, including two ambiguous cases. The remaining definite false negative
was Mimic's Senior Mechanical Engineer – Wearable Devices
(`420ee90e-ef8b-4337-a123-9c6b35bd4983`), a wearable used to collect robot-training
data (accept probability 0.21). The user explicitly accepted this known remaining
miss and authorized deployment. This limitation is recorded internally, not in
public article copy.

A separate shorter-query replay with the original builder scored 172/176 and was
not adopted. Metadata audit found one source discrepancy: Distalmotion's 3–5
experience years had been indexed as minimum 5 with unspecified maximum. The
sentinel 99 and missing salary values were omitted correctly. No unrelated
occupation/industry taxonomy was present in these model inputs; the query had no
experience constraint. This does not establish the cause of any decision.

Earlier query revisions encountered ambiguous transport/timeout failures. Safe
logs retained no HTTP status for those attempts; exact network cause remains
unknown. This change does not alter transport or billing recovery.

## Versioning and rollout

Prompt version `jev-job-fit-choice-v2` separates new cache identities from v1.
The normal authenticated owner reconcile saves unchanged text as a fresh query
revision when model, prompt, schema or normalizer versions differ. Subsequent
same-version saves remain idempotent. Existing query versions, decisions, cache
rows and usage/budget history remain intact; the existing configuration migration
cancels superseded active segments.

A durable worker refuses to claim a query from another classifier version, without
mutating its segments (it may belong to a newer deployment). Spend authorization
also requires exact current version fields. Normal owner refresh performs the
migration and catch-up; anonymous readers never initiate paid refresh.

Focused regressions cover the exact evaluated request shape and untrusted-data
boundary, prompt cache separation, version-only migration/idempotency/history,
stale durable-step refusal and spend-time version authorization. Deployment must
be followed by normal owner View refresh and persisted completion verification.
