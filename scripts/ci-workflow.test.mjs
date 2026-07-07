import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const workflow = readFileSync(".github/workflows/ci.yml", "utf8");

test("CI change detection uses the pinned paths-filter action", () => {
  assert.match(
    workflow,
    /uses: dorny\/paths-filter@d1c1ffe0248fe513906c8e24db8ea791d46f8590 # v3/,
  );
  assert.match(workflow, /predicate-quantifier: every/);
  assert.match(workflow, /code:\n(?:              - .+\n)+/);
  assert.match(workflow, /crawler_code:\n(?:              - .+\n)+/);
  assert.match(workflow, /boards_csv:\n              - 'apps\/crawler\/data\/boards\.csv'/);
});

test("CI change detection preserves the existing non-code exclusions", () => {
  for (const pattern of [
    "'!**/*.md'",
    "'!docs/**'",
    "'!.github/dependabot.yml'",
    "'!.github/dependabot.yaml'",
    "'!.github/ISSUE_TEMPLATE/**'",
    "'!.github/DISCUSSION_TEMPLATE/**'",
    "'!apps/crawler/data/**'",
    "'!apps/crawler/traces/**'",
    "'!apps/crawler/VERSION'",
  ]) {
    assert.ok(workflow.includes(pattern), `missing filter pattern ${pattern}`);
  }
});

test("CI no longer shells out to custom diff classification", () => {
  assert.equal(workflow.includes("scripts/ci-classify-changes.mjs"), false);
  assert.equal(workflow.includes("gh api --paginate"), false);
  assert.equal(workflow.includes("git diff --name-only"), false);
  assert.equal(workflow.includes("git diff-tree"), false);
});

test("workflow-security runs repository script tests", () => {
  assert.match(
    workflow,
    /node --test\n          scripts\/ci-workflow\.test\.mjs\n          scripts\/dealroom-company-requests\.test\.mjs/,
  );
});
