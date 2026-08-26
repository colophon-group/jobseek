# Runtime migration exact-head approval guard

The `Runtime Review Attestation` required status prevents a crawler runtime
migration PR from retaining approval after its head commit changes. It was
introduced after PR #8060 gained a late commit after the recorded review head.

## Security claim

This is an accidental-drift guard, not an identity or cryptographic
attestation system. All current agents use the same `viktor-shcherb` GitHub
credential. The workflow cannot prove which agent supplied an approval, prove
writer/reviewer independence, or resist deliberate forgery by someone holding
that credential. Those properties require a separately credentialed app,
broker, or human principal and are explicitly out of scope.

The repository control provides two narrower guarantees:

- evaluation and record publication execute code checked out from the default
  branch, never code from the PR;
- the required status succeeds only when the latest unedited workflow-published
  JSON record is an approval naming the exact live PR head. The tree SHA is
  checked diagnostic metadata and never substitutes for commit identity.

## Append-only records

Machine comments begin with
`<!-- jobseek-runtime-review-event:v1 -->` and contain one JSON object. A head
observation invalidates prior approval whenever GitHub reports a synchronization
or when a machine comment is edited or deleted. This append-only transition is
why moving from head H0 to H1 and then back to H0 cannot reactivate H0's old
approval.

An approval record contains the schema, repository, PR number, approved head
and tree SHAs, outcome, review sequence, repair count, and optional
repaired-from head. Sequence 1 is the initial review with repair count 0.
Sequence 2 is the only bounded repair, with repair count 1 and an exact
predecessor SHA. The gate rejects counter rollback, mismatched lineage, changed
content after the repair, malformed or edited records, and duplicate approval
for a previously approved head.

Publish a freshly observed initial approval from the default branch:

```bash
gh workflow run runtime-review-attestation.yml \
  --ref main \
  -f pr=<PR> \
  -f expected_head_sha=<40-character-live-head> \
  -f review_sequence=1 \
  -f repair_count=0
```

After the single bounded repair, use sequence 2, repair count 1, and
`-f repaired_from_head=<previous-approved-head>`. The publisher re-reads the
head immediately before appending the record. The record's comment-created
event then causes a fresh evaluation.

Every synchronize, rebase, force-push, or new commit makes the prior record
stale. A different commit with the same tree also remains stale. Ready state,
labels, PR prose, and a successful status on an older commit grant no
authority. The evaluator re-reads the live PR head immediately before posting
its status and fails the newly observed head if it moved during evaluation.

## Activation

`.github/rulesets/main-strict-gate.json` declares `Runtime Review Attestation`
as a required context. Updating that checked-in file does not mutate GitHub's
live ruleset. After this change reaches `main`, an authorized operator must
reconcile the live `main-strict-gate` ruleset separately, then verify an
in-scope test PR cannot merge with a missing or stale record. The next
migration lane remains blocked until that post-merge activation succeeds.
