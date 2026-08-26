import assert from "node:assert/strict";
import test from "node:test";

import {
  BOT_LOGIN,
  MARKER,
  SCHEMA,
  evaluateApproval,
  isRuntimeMigrationPath,
  validateTransition,
} from "../.github/scripts/runtime-review-attestation.mjs";

const repository = "colophon-group/jobseek";
const pullRequest = 8060;
const reviewed = "1b0e7f2ec00212088396a5d9a98b4d702d5111b8";
const late = "b0816915b01748296506ae587b52347cc4e262c9";
const rebased = "9c5b0e77a266e2b57decb22c5cc303bd181a9089";
const forcePushed = "a".repeat(40);
const tree = "15f2be386de875b7684f45222268962857700c32";
const changedTree = "b".repeat(40);

function approval({
  head = reviewed,
  headTree = tree,
  sequence = 1,
  repairCount = 0,
  repairedFrom = null,
} = {}) {
  return {
    schema: SCHEMA,
    kind: "approval",
    repository,
    pull_request: pullRequest,
    approved_head_sha: head,
    head_tree_sha: headTree,
    outcome: "approved",
    review_sequence: sequence,
    repair_count: repairCount,
    repaired_from_head: repairedFrom,
  };
}

function observation(head, headTree) {
  return {
    schema: SCHEMA,
    kind: "head_observed",
    repository,
    pull_request: pullRequest,
    approved_head_sha: head,
    head_tree_sha: headTree,
    outcome: "invalidated",
    review_sequence: null,
    repair_count: null,
    repaired_from_head: null,
  };
}

function comment(record, { id = 1, edited = false, login = BOT_LOGIN } = {}) {
  return {
    id,
    user: { login },
    created_at: "2026-08-26T18:00:00Z",
    updated_at: edited ? "2026-08-26T18:01:00Z" : "2026-08-26T18:00:00Z",
    body: `${MARKER}\n${JSON.stringify(record)}`,
  };
}

function evaluate({
  headAtStart = reviewed,
  treeAtStart = tree,
  headAtConclusion = headAtStart,
  comments = [comment(approval())],
  changedPaths = ["apps/crawler/contracts/v1/runtime.proto"],
} = {}) {
  return evaluateApproval({
    repository,
    pullRequest,
    headAtStart,
    treeAtStart,
    headAtConclusion,
    comments,
    changedPaths,
  });
}

test("allows one valid approval only on the exact current head", () => {
  assert.deepEqual(evaluate(), {
    eligible: true,
    scoped: true,
    reason: "exact head approved at review sequence 1",
  });
});

test("reproduces #8060 late-commit stale approval", () => {
  const result = evaluate({ headAtStart: late, treeAtStart: changedTree });
  assert.equal(result.eligible, false);
  assert.match(result.reason, /stale: current commit changed$/);
});

test("same-tree new commits and ordinary or force-pushed rebases are stale", () => {
  for (const head of [late, rebased, forcePushed]) {
    const result = evaluate({ headAtStart: head, treeAtStart: tree });
    assert.equal(result.eligible, false);
    assert.match(result.reason, /tree is identical/);
  }
});

test("H0 to H1 to H0 cannot reactivate the old H0 approval", () => {
  const result = evaluate({
    comments: [
      comment(approval(), { id: 1 }),
      comment(observation(late, changedTree), { id: 2 }),
      comment(observation(reviewed, tree), { id: 3 }),
    ],
  });
  assert.equal(result.eligible, false);
  assert.match(
    result.reason,
    /latest head observation has no current approval/,
  );
});

test("head movement during evaluation fails before any approval can pass", () => {
  const result = evaluate({ headAtConclusion: late });
  assert.equal(result.eligible, false);
  assert.match(result.reason, /changed while.*evaluating/);
});

test("missing or deleted current approval fails closed", () => {
  assert.match(evaluate({ comments: [] }).reason, /missing/);
  assert.match(
    evaluate({ comments: [comment(observation(reviewed, tree))] }).reason,
    /no current approval/,
  );
});

test("malformed, edited, and non-workflow records fail closed", () => {
  const malformed = { ...comment(approval()), body: `${MARKER}\n{` };
  assert.match(evaluate({ comments: [malformed] }).reason, /malformed/);
  assert.match(
    evaluate({ comments: [comment(approval(), { edited: true })] }).reason,
    /edited/,
  );
  assert.match(
    evaluate({ comments: [comment(approval(), { login: "viktor-shcherb" })] })
      .reason,
    /trusted workflow/,
  );
});

test("duplicate or conflicting records for one head fail closed", () => {
  const result = evaluate({
    comments: [comment(approval(), { id: 1 }), comment(approval(), { id: 2 })],
  });
  assert.equal(result.eligible, false);
  assert.match(result.reason, /already exists|multiple or conflicting/);
});

test("one bounded repair succeeds with exact predecessor lineage", () => {
  const repaired = approval({
    head: late,
    headTree: changedTree,
    sequence: 2,
    repairCount: 1,
    repairedFrom: reviewed,
  });
  const result = evaluate({
    headAtStart: late,
    treeAtStart: changedTree,
    comments: [
      comment(approval(), { id: 1 }),
      comment(observation(late, changedTree), { id: 2 }),
      comment(repaired, { id: 3 }),
    ],
  });
  assert.equal(result.eligible, true);
  assert.match(result.reason, /sequence 2/);
});

test("repair rollback, mismatched lineage, and a second repair are rejected", () => {
  const repaired = approval({
    head: late,
    headTree: changedTree,
    sequence: 2,
    repairCount: 1,
    repairedFrom: reviewed,
  });
  const rollback = evaluate({
    comments: [
      comment(repaired, { id: 1 }),
      comment(observation(forcePushed, tree), { id: 2 }),
      comment(approval({ head: forcePushed }), { id: 3 }),
    ],
    headAtStart: forcePushed,
  });
  assert.match(rollback.reason, /rolls the repair count back/);

  const badLineage = validateTransition(
    approval(),
    approval({
      head: late,
      headTree: changedTree,
      sequence: 2,
      repairCount: 1,
      repairedFrom: forcePushed,
    }),
  );
  assert.match(badLineage, /predecessor/);

  const second = validateTransition(
    repaired,
    approval({
      head: forcePushed,
      headTree: "c".repeat(40),
      sequence: 2,
      repairCount: 1,
      repairedFrom: reviewed,
    }),
  );
  assert.match(second, /budget was spent/);
});

test("same-tree fresh approval preserves the existing repair budget", () => {
  assert.equal(
    validateTransition(approval(), approval({ head: rebased })),
    null,
  );
  const repaired = approval({
    head: late,
    headTree: changedTree,
    sequence: 2,
    repairCount: 1,
    repairedFrom: reviewed,
  });
  assert.equal(
    validateTransition(
      repaired,
      approval({
        head: forcePushed,
        headTree: changedTree,
        sequence: 2,
        repairCount: 1,
        repairedFrom: reviewed,
      }),
    ),
    null,
  );
});

test("scope includes contracts, Go, and the guard itself only", () => {
  for (const path of [
    "apps/crawler/contracts/v1/runtime.proto",
    "apps/crawler/internal/worker/worker.go",
    "apps/crawler/go.mod",
    ".github/workflows/runtime-review-attestation.yml",
  ]) {
    assert.equal(isRuntimeMigrationPath(path), true, path);
  }
  assert.equal(isRuntimeMigrationPath("apps/crawler/data/boards.csv"), false);
  assert.equal(isRuntimeMigrationPath("apps/web/app/page.tsx"), false);
  assert.equal(
    evaluate({ changedPaths: ["apps/web/app/page.tsx"] }).eligible,
    true,
  );
});
