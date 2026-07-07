import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const workflow = readFileSync(".github/workflows/ci.yml", "utf8");
const uploadCompanyImagesWorkflow = readFileSync(
  ".github/workflows/upload-company-images.yml",
  "utf8",
);
const publishMcpServerWorkflow = readFileSync(
  ".github/workflows/publish-mcp-server.yml",
  "utf8",
);

function setupUvBlocks(workflowSource) {
  return [
    ...workflowSource.matchAll(
      /- uses: astral-sh\/setup-uv@[^\n]+[\s\S]*?(?=\n      - |\n  [a-zA-Z0-9_-]+:|\n$)/g,
    ),
  ].map((match) => match[0]);
}

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

test("setup-uv steps cache uv downloads by crawler lockfile", () => {
  const checkedWorkflows = {
    ci: workflow,
    "upload-company-images": uploadCompanyImagesWorkflow,
  };

  for (const [name, source] of Object.entries(checkedWorkflows)) {
    const blocks = setupUvBlocks(source);
    assert.ok(blocks.length > 0, `${name} should use setup-uv`);

    for (const block of blocks) {
      assert.match(block, /enable-cache: true/);
      assert.match(block, /cache-dependency-glob: "apps\/crawler\/uv\.lock"/);
    }
  }
});

test("MCP publish workflow caches the pnpm store", () => {
  assert.match(
    publishMcpServerWorkflow,
    /pnpm\/action-setup@0ebf47130e4866e96fce0953f49152a61190b271 # v6\.0\.9[\s\S]*actions\/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e # v6/,
  );
  assert.match(publishMcpServerWorkflow, /cache: pnpm/);
  assert.match(publishMcpServerWorkflow, /cache-dependency-path: pnpm-lock\.yaml/);
});
