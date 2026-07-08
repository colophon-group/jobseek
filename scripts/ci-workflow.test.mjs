import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const workflow = readFileSync(".github/workflows/ci.yml", "utf8");
const codeqlWorkflow = readFileSync(".github/workflows/codeql.yml", "utf8");
const uploadCompanyImagesWorkflow = readFileSync(
  ".github/workflows/upload-company-images.yml",
  "utf8",
);
const maybeAutoMergeWorkflow = readFileSync(
  ".github/workflows/maybe-auto-merge.yml",
  "utf8",
);
const maybeAutoMergeScript = readFileSync(
  ".github/scripts/maybe-auto-merge-pr.sh",
  "utf8",
);
const classifyPrPathsScript = readFileSync(
  ".github/scripts/classify-pr-paths.sh",
  "utf8",
);
const dispatchPrChecksScript = readFileSync(
  ".github/scripts/dispatch-pr-checks.sh",
  "utf8",
);
const labelPrScript = readFileSync(".github/scripts/label-pr.sh", "utf8");
const publishMcpServerWorkflow = readFileSync(
  ".github/workflows/publish-mcp-server.yml",
  "utf8",
);
const mainStrictGateRuleset = JSON.parse(
  readFileSync(".github/rulesets/main-strict-gate.json", "utf8"),
);

function setupUvBlocks(workflowSource) {
  return [
    ...workflowSource.matchAll(
      /- uses: astral-sh\/setup-uv@[^\n]+[\s\S]*?(?=\n      - |\n  [a-zA-Z0-9_-]+:|\n$)/g,
    ),
  ].map((match) => match[0]);
}

function jobBlock(jobId) {
  const match = workflow.match(
    new RegExp(`\\n  ${jobId}:\\n[\\s\\S]*?(?=\\n  [a-zA-Z0-9_-]+:\\n|\\n$)`),
  );
  assert.ok(match, `missing workflow job ${jobId}`);
  return match[0];
}

function workflowJobBlock(workflowSource, jobId) {
  const match = workflowSource.match(
    new RegExp(`\\n  ${jobId}:\\n[\\s\\S]*?(?=\\n  [a-zA-Z0-9_-]+:\\n|\\n$)`),
  );
  assert.ok(match, `missing workflow job ${jobId}`);
  return match[0];
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

test("manual CI dispatch can classify a PR without full code checks", () => {
  const changesJob = jobBlock("changes");
  assert.match(workflow, /workflow_dispatch:\n    inputs:\n      pr:/);
  assert.match(changesJob, /id: manual-default/);
  assert.match(changesJob, /id: manual-pr/);
  assert.match(changesJob, /\.github\/scripts\/classify-pr-paths\.sh/);
  assert.match(classifyPrPathsScript, /gh api --paginate "repos\/\$REPO\/pulls\/\$PR\/files"/);
  assert.match(classifyPrPathsScript, /emit "code" "\$code"/);
  assert.match(classifyPrPathsScript, /emit "crawler_code" "\$crawler_code"/);
  assert.match(classifyPrPathsScript, /emit "boards_csv" "\$boards_csv"/);
  assert.match(classifyPrPathsScript, /emit "codeql" "\$code"/);
});

test("workflow-security runs repository script tests", () => {
  assert.match(workflow, /node --test/);
  assert.match(workflow, /scripts\/ci-workflow\.test\.mjs/);
  assert.match(workflow, /scripts\/docs-index\.test\.mjs/);
  assert.match(workflow, /scripts\/dealroom-company-requests\.test\.mjs/);
});

test("maybe-auto-merge wakes without manual retries", () => {
  const job = workflowJobBlock(maybeAutoMergeWorkflow, "label-and-merge");
  assert.match(maybeAutoMergeWorkflow, /workflow_run:\n    workflows: \["CI", "CodeQL"\]/);
  assert.match(maybeAutoMergeWorkflow, /schedule:\n    - cron: "\*\/15 \* \* \* \*"/);
  assert.match(maybeAutoMergeWorkflow, /workflow_dispatch:/);
  assert.match(job, /name: Select PRs/);
  assert.match(job, /select_open_company_prs\(\)/);
  assert.match(job, /\$branch" == "\$default_branch"/);
  assert.match(job, /name: Label, rebase, and merge/);
  assert.match(job, /maybe-auto-merge-pr\.sh/);
});

test("maybe-auto-merge script skips image PRs and retries pending merges", () => {
  assert.match(maybeAutoMergeScript, /apps\/crawler\/data\/images\//);
  assert.match(maybeAutoMergeScript, /upload-company-images will handle it/);
  assert.match(maybeAutoMergeScript, /label-pr\.sh/);
  assert.match(maybeAutoMergeScript, /git rebase origin\/main/);
  assert.match(maybeAutoMergeScript, /dispatch-pr-checks\.sh/);
  assert.match(maybeAutoMergeScript, /gh pr merge "\$PR" --repo "\$REPO" --rebase/);
  assert.match(maybeAutoMergeScript, /scheduled\/workflow_run retries will revisit it/);
});

test("bot-authored company branch updates dispatch path-aware CI", () => {
  assert.match(dispatchPrChecksScript, /gh workflow run ci\.yml --repo "\$REPO" --ref "\$branch" -f "pr=\$PR"/);
  assert.doesNotMatch(dispatchPrChecksScript, /codeql\.yml/);
  assert.match(dispatchPrChecksScript, /"\$branch" != add-company\/\*/);

  assert.match(maybeAutoMergeWorkflow, /actions: write/);
  assert.match(maybeAutoMergeWorkflow, /dispatch-pr-checks\.sh/);

  assert.match(uploadCompanyImagesWorkflow, /actions: write/);
  assert.match(uploadCompanyImagesWorkflow, /id: image-sync/);
  assert.match(uploadCompanyImagesWorkflow, /steps\.image-sync\.outputs\.pushed == 'true'/);
  assert.match(uploadCompanyImagesWorkflow, /Dispatch checks for image commit/);
  assert.match(uploadCompanyImagesWorkflow, /Auto merge is not allowed for this repository/);
});

test("company PR label script applies decision labels idempotently", () => {
  assert.match(labelPrScript, /gh pr view "\$PR" --repo "\$REPO" --json labels/);
  assert.match(labelPrScript, /DESIRED_LABELS=",\$LABELS,"/);
  assert.match(labelPrScript, /has_desired_label\(\)/);
  assert.doesNotMatch(labelPrScript, /declare -A/);
  assert.match(labelPrScript, /Removing stale label:/);
  assert.match(labelPrScript, /Adding label:/);
  assert.doesNotMatch(
    labelPrScript,
    /for L in \$ALL_DECISION_LABELS; do\s+gh pr edit "\$PR" --repo "\$REPO" --remove-label "\$L"/,
  );
});

test("CodeQL skips full analysis for non-code pull requests", () => {
  const changesJob = workflowJobBlock(codeqlWorkflow, "changes");
  assert.match(codeqlWorkflow, /pull_request:\n    branches: \[main\]\n    paths-ignore:/);
  assert.match(changesJob, /name: Detect CodeQL changes/);
  assert.match(changesJob, /id: manual-pr/);
  assert.match(changesJob, /\.github\/scripts\/classify-pr-paths\.sh/);
  assert.match(changesJob, /uses: dorny\/paths-filter@d1c1ffe0248fe513906c8e24db8ea791d46f8590 # v3/);
  assert.match(changesJob, /predicate-quantifier: every/);
  assert.match(changesJob, /codeql:\n(?:              - .+\n)+/);

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
    assert.ok(changesJob.includes(pattern), `missing CodeQL filter pattern ${pattern}`);
  }

  const analyzeJob = workflowJobBlock(codeqlWorkflow, "analyze");
  assert.match(analyzeJob, /name: Analyze \(\$\{\{ matrix\.language \}\}\)/);
  assert.match(analyzeJob, /needs: changes/);
  assert.doesNotMatch(analyzeJob, /\n    if: needs\.changes\.outputs\.codeql/);
  assert.match(analyzeJob, /name: Skip CodeQL analysis for non-code PR/);
  assert.match(analyzeJob, /if: needs\.changes\.outputs\.codeql != 'true'/);
  assert.match(analyzeJob, /Initialize CodeQL[\s\S]*if: needs\.changes\.outputs\.codeql == 'true'/);
  assert.match(analyzeJob, /Perform CodeQL Analysis[\s\S]*if: needs\.changes\.outputs\.codeql == 'true'/);
});

test("main branch ruleset does not require non-path-aware code scanning", () => {
  assert.equal(mainStrictGateRuleset.name, "main-strict-gate");
  assert.equal(
    mainStrictGateRuleset.rules.some((rule) => rule.type === "code_scanning"),
    false,
  );

  const statusRule = mainStrictGateRuleset.rules.find(
    (rule) => rule.type === "required_status_checks",
  );
  assert.ok(statusRule, "main-strict-gate should require status checks");
  assert.equal(statusRule.parameters.strict_required_status_checks_policy, false);
  const contexts = statusRule.parameters.required_status_checks.map(
    (check) => check.context,
  );

  assert.deepEqual(contexts, ["Required CI"]);
  assert.equal(
    Object.hasOwn(statusRule.parameters.required_status_checks[0], "integration_id"),
    false,
  );
});

test("workflow-dispatched CI publishes the Required CI status context", () => {
  const requiredCiJob = jobBlock("required-ci");
  assert.match(requiredCiJob, /permissions:\n      statuses: write/);
  assert.match(requiredCiJob, /INPUT_PR: \$\{\{ github\.event\.inputs\.pr \|\| '' \}\}/);
  assert.match(requiredCiJob, /if \[\[ "\$EVENT_NAME" == "workflow_dispatch" && -n "\$INPUT_PR" \]\]/);
  assert.match(requiredCiJob, /repos\/\$GITHUB_REPOSITORY\/statuses\/\$GITHUB_SHA/);
  assert.match(requiredCiJob, /-f context="Required CI"/);
  assert.match(requiredCiJob, /exit "\$status"/);
});

test("CI runs Typesense E2E suites against a service container", () => {
  const webJob = jobBlock("test-web-typesense-e2e");
  assert.match(webJob, /services:\n      typesense:/);
  assert.match(webJob, /image: typesense\/typesense:27\.1/);
  assert.match(webJob, /options: --tmpfs \/data:rw/);
  assert.match(webJob, /TYPESENSE_API_KEY: local_dev_typesense_key/);
  assert.match(webJob, /TYPESENSE_DATA_DIR: \/data/);
  assert.match(webJob, /REQUIRE_TYPESENSE_E2E: "true"/);
  assert.match(webJob, /name: Wait for Typesense[\s\S]*curl -fsS http:\/\/localhost:8108\/health/);
  assert.match(
    webJob,
    /pnpm --filter @jobseek\/web exec vitest run src\/lib\/search\/__tests__\/typesense\.e2e\.test\.ts/,
  );

  const crawlerJob = jobBlock("test-crawler-typesense-e2e");
  assert.match(crawlerJob, /services:\n      typesense:/);
  assert.match(crawlerJob, /image: typesense\/typesense:27\.1/);
  assert.match(crawlerJob, /options: --tmpfs \/data:rw/);
  assert.match(crawlerJob, /TYPESENSE_DATA_DIR: \/data/);
  assert.match(crawlerJob, /TYPESENSE_ADMIN_KEY: local_dev_typesense_key/);
  assert.match(crawlerJob, /REQUIRE_TYPESENSE_E2E: "true"/);
  assert.match(
    crawlerJob,
    /name: Wait for Typesense[\s\S]*curl -fsS http:\/\/localhost:8108\/health/,
  );
  assert.match(crawlerJob, /uv run python \.\.\/\.\.\/scripts\/typesense-setup\.py --force/);
  assert.match(crawlerJob, /uv run pytest tests\/e2e\/test_typesense_indexing\.py -v/);
});

test("broad CI test jobs exclude service-backed Typesense E2E suites", () => {
  const webJob = jobBlock("test-web");
  assert.match(
    webJob,
    /pnpm --filter @jobseek\/web exec vitest run[\s\S]*--exclude src\/lib\/search\/__tests__\/typesense\.e2e\.test\.ts/,
  );

  const crawlerJob = jobBlock("test-crawler");
  assert.match(crawlerJob, /uv run pytest tests\/ -v --ignore=tests\/e2e\/test_typesense_indexing\.py/);

  const coverageWebJob = jobBlock("coverage-web");
  assert.match(
    coverageWebJob,
    /pnpm --filter @jobseek\/web exec vitest run[\s\S]*--config vitest\.coverage\.config\.ts[\s\S]*--exclude src\/lib\/search\/__tests__\/typesense\.e2e\.test\.ts/,
  );
});

test("Required CI gates Typesense E2E jobs", () => {
  assert.match(workflow, /needs:[\s\S]*- test-web-typesense-e2e/);
  assert.match(workflow, /needs:[\s\S]*- test-crawler-typesense-e2e/);
  assert.match(workflow, /"test-web-typesense-e2e"/);
  assert.match(workflow, /"test-crawler-typesense-e2e"/);
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
