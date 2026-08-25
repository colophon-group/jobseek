import assert from "node:assert/strict";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const workflow = readFileSync(".github/workflows/ci.yml", "utf8");
const webBuildEnvAction = readFileSync(
  ".github/actions/setup-web-build-env/action.yml",
  "utf8",
);
const codeqlWorkflow = readFileSync(".github/workflows/codeql.yml", "utf8");
const dependabotConfig = readFileSync(".github/dependabot.yml", "utf8");
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
const dispatchCompanyProductionSyncScript = readFileSync(
  ".github/scripts/dispatch-company-production-sync.sh",
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
const labelPrCsvDiffHelper = ".github/scripts/label_pr_csv_diff.py";
const publishMcpServerWorkflow = readFileSync(
  ".github/workflows/publish-mcp-server.yml",
  "utf8",
);
const deployCodexRunnerWorkflow = readFileSync(
  ".github/workflows/deploy-codex-runner.yml",
  "utf8",
);
const deployCrawlerWorkflow = readFileSync(
  ".github/workflows/deploy-crawler-browser.yml",
  "utf8",
);
const crawlerDeployScript = readFileSync("apps/crawler/deploy.sh", "utf8");
const crawlerMaintenanceScript = readFileSync(
  "scripts/jobseek-maintenance.py",
  "utf8",
);
const deployDataBackupsWorkflow = readFileSync(
  ".github/workflows/deploy-data-backups.yml",
  "utf8",
);
const deployTypesenseHostWorkflow = readFileSync(
  ".github/workflows/deploy-typesense-host.yml",
  "utf8",
);
const deployCodexRunnerHostScript = readFileSync(
  "scripts/deploy-codex-runner-host.sh",
  "utf8",
);
const crawlerScheduledMaintenanceWorkflow = readFileSync(
  ".github/workflows/crawler-scheduled-maintenance.yml",
  "utf8",
);
const syncDataWorkflow = readFileSync(
  ".github/workflows/sync-data.yml",
  "utf8",
);
const crawlerCsvSyncHostScript = readFileSync(
  "scripts/crawler-csv-sync-host.sh",
  "utf8",
);
const refreshCurrencyRatesWorkflow = readFileSync(
  ".github/workflows/refresh-currency-rates.yml",
  "utf8",
);
const crawlerHostHygieneScript = readFileSync(
  "scripts/crawler-host-hygiene.py",
  "utf8",
);
const mainStrictGateRuleset = JSON.parse(
  readFileSync(".github/rulesets/main-strict-gate.json", "utf8"),
);

function runDispatchPrChecks({
  state = "OPEN",
  isDraft = false,
  branch = "add-company/example",
  owner = "colophon-group",
  requestedBranch = branch,
} = {}) {
  const dir = mkdtempSync(join(tmpdir(), "dispatch-pr-checks-"));
  const log = join(dir, "gh.log");
  const gh = join(dir, "gh");
  writeFileSync(
    gh,
    `#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "pr view" ]]; then
  printf '%s' "$MOCK_PR_JSON"
  exit 0
fi
printf '%s\\n' "$*" >> "$MOCK_GH_LOG"
`,
  );
  chmodSync(gh, 0o755);
  const env = {
    ...process.env,
    PATH: `${dir}:${process.env.PATH}`,
    GH_TOKEN: "test-token",
    REPO: "colophon-group/jobseek",
    PR: "123",
    MOCK_GH_LOG: log,
    MOCK_PR_JSON: JSON.stringify({
      state,
      isDraft,
      headRefName: branch,
      headRepositoryOwner: { login: owner },
    }),
  };
  if (requestedBranch !== null) env.BRANCH = requestedBranch;
  const result = spawnSync("bash", [".github/scripts/dispatch-pr-checks.sh"], {
    cwd: process.cwd(),
    env,
    encoding: "utf8",
  });
  let calls = "";
  try {
    calls = readFileSync(log, "utf8");
  } catch {
    // A correctly skipped PR does not call `gh workflow run`.
  }
  rmSync(dir, { recursive: true, force: true });
  return { ...result, calls };
}

function runDispatchCompanyProductionSync({
  defaultBranch = "main",
  includeDefaultBranch = true,
  prewarmSha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  prewarmWatchStatus = 0,
} = {}) {
  const dir = mkdtempSync(join(tmpdir(), "dispatch-company-sync-"));
  const log = join(dir, "gh.log");
  const gh = join(dir, "gh");
  writeFileSync(
    gh,
    `#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$MOCK_GH_LOG"
if [[ "$1 $2" == "run list" ]]; then
  if [[ "$*" == *"--workflow prewarm-company-og-cache.yml"* ]]; then
    printf '4242\\t%s\\n' "$MOCK_PREWARM_SHA"
  else
    printf '4343\\n'
  fi
elif [[ "$1 $2" == "run watch" ]]; then
  exit "$MOCK_PREWARM_WATCH_STATUS"
fi
`,
  );
  chmodSync(gh, 0o755);
  const env = {
    ...process.env,
    PATH: `${dir}:${process.env.PATH}`,
    GH_TOKEN: "test-token",
    REPO: "colophon-group/jobseek",
    PR: "123",
    MOCK_GH_LOG: log,
    MOCK_PREWARM_SHA: prewarmSha,
    MOCK_PREWARM_WATCH_STATUS: String(prewarmWatchStatus),
  };
  if (includeDefaultBranch) env.DEFAULT_BRANCH = defaultBranch;
  const result = spawnSync(
    "bash",
    [".github/scripts/dispatch-company-production-sync.sh"],
    {
      cwd: process.cwd(),
      env,
      encoding: "utf8",
    },
  );
  const calls = readFileSync(log, "utf8");
  rmSync(dir, { recursive: true, force: true });
  return { ...result, calls };
}

function runClassifyPrPaths({ files = [], baseRef = "main" } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "classify-pr-paths-"));
  const output = join(dir, "github-output");
  const gh = join(dir, "gh");
  writeFileSync(
    gh,
    `#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"/files"* ]]; then
  printf '%s\\n' "$MOCK_FILES"
else
  printf '%s\\n' "$MOCK_BASE_REF"
fi
`,
  );
  chmodSync(gh, 0o755);
  const result = spawnSync("bash", [".github/scripts/classify-pr-paths.sh"], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      PATH: `${dir}:${process.env.PATH}`,
      GH_TOKEN: "test-token",
      REPO: "colophon-group/jobseek",
      PR: "123",
      GITHUB_OUTPUT: output,
      MOCK_FILES: files.join("\n"),
      MOCK_BASE_REF: baseRef,
    },
    encoding: "utf8",
  });
  let outputs = "";
  try {
    outputs = readFileSync(output, "utf8");
  } catch {
    // Failed classifications may stop before writing outputs.
  }
  rmSync(dir, { recursive: true, force: true });
  return { ...result, outputs };
}

function runCompanyPrLabeler(diff) {
  const dir = mkdtempSync(join(tmpdir(), "label-company-pr-"));
  const output = join(dir, "github-output");
  const log = join(dir, "gh.log");
  const gh = join(dir, "gh");
  writeFileSync(
    gh,
    `#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "pr view" && "$*" == *"headRefName"* ]]; then
  printf '%s\n' 'add-company/example'
elif [[ "$1 $2" == "pr view" && "$*" == *"labels"* ]]; then
  printf '%s\n' 'review-code'
elif [[ "$1 $2" == "pr diff" && "$*" == *"--name-only"* ]]; then
  printf '%s\n' 'apps/crawler/data/boards.csv' 'apps/crawler/data/companies.csv' 'apps/crawler/data/company_descriptions.csv'
elif [[ "$1 $2" == "pr diff" ]]; then
  printf '%s' "$MOCK_DIFF"
elif [[ "$1" == "api" && "$*" == *"/comments"* ]]; then
  printf '%s\n' '<!-- crawl-stats {"jobs": 10, "monitor_time": 1.0} -->'
elif [[ "$1" == "api" && "$*" == *"/contents/"* ]]; then
  exit 0
elif [[ "$1 $2" == "label create" ]]; then
  exit 0
elif [[ "$1 $2" == "pr edit" ]]; then
  printf '%s\n' "$*" >> "$MOCK_GH_LOG"
else
  printf 'unexpected gh call: %s\n' "$*" >&2
  exit 2
fi
`,
  );
  chmodSync(gh, 0o755);
  const result = spawnSync("bash", [".github/scripts/label-pr.sh"], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      PATH: `${dir}:${process.env.PATH}`,
      GH_TOKEN: "test-token",
      REPO: "colophon-group/jobseek",
      PR: "123",
      GITHUB_OUTPUT: output,
      MOCK_DIFF: diff,
      MOCK_GH_LOG: log,
    },
    encoding: "utf8",
  });
  let outputs = "";
  let calls = "";
  try {
    outputs = readFileSync(output, "utf8");
  } catch {
    // A failed classifier may stop before publishing outputs.
  }
  try {
    calls = readFileSync(log, "utf8");
  } catch {
    // No label mutations means the mock call log is absent.
  }
  rmSync(dir, { recursive: true, force: true });
  return { ...result, outputs, calls };
}

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

function durationSeconds(value) {
  const match = value.match(/^(\d+)([smh])$/);
  assert.ok(match, `unsupported duration ${value}`);
  const unitSeconds = { s: 1, m: 60, h: 3600 };
  return Number(match[1]) * unitSeconds[match[2]];
}

test("CI change detection uses the pinned paths-filter action", () => {
  assert.match(
    workflow,
    /uses: dorny\/paths-filter@ceb8a2b8f2d89434be7ff52d3de7ec3738c5cc9d # v4\.0\.3/,
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

test("manual PR classification exports the validated PR base context", () => {
  const result = runClassifyPrPaths({
    files: ["apps/crawler/src/core/monitor.py", "apps/crawler/data/boards.csv"],
    baseRef: "main",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.outputs, /^code=true$/m);
  assert.match(result.outputs, /^crawler_code=true$/m);
  assert.match(result.outputs, /^boards_csv=true$/m);
  assert.match(result.outputs, /^is_pr=true$/m);
  assert.match(result.outputs, /^base_ref=main$/m);
});

test("runtime taxonomies and contract derivation require crawler version gates", () => {
  for (const file of [
    "apps/crawler/data/industries.csv",
    "apps/crawler/data/occupations.csv",
    "apps/crawler/data/seniority.csv",
    "apps/crawler/data/technologies.csv",
    "scripts/derive-crawler-runtime-contract.mjs",
  ]) {
    const result = runClassifyPrPaths({ files: [file], baseRef: "main" });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.outputs, /^code=true$/m, file);
    assert.match(result.outputs, /^crawler_code=true$/m, file);
  }
  assert.match(
    workflow,
    /crawler_runtime_boundary:\n\s+- '\{apps\/crawler\/data\/\{industries,occupations,seniority,technologies\}\.csv,scripts\/derive-crawler-runtime-contract\.mjs\}'/,
  );
  assert.match(workflow, /id: filter-combined[\s\S]*CRAWLER_RUNTIME_BOUNDARY/);
});

test("PR-only CI gates cover pull requests and dispatched PRs", () => {
  const changesJob = jobBlock("changes");
  const crawlerImageJob = jobBlock("crawler-image");
  const versionJob = jobBlock("version-check");
  const probeJob = jobBlock("probe-new-boards");
  const requiredCiJob = jobBlock("required-ci");

  assert.match(changesJob, /echo "is_pr=false"/);
  assert.match(changesJob, /id: manual-pr[\s\S]*classify-pr-paths\.sh/);
  assert.match(changesJob, /id: pull-request[\s\S]*echo "is_pr=true"/);
  assert.match(changesJob, /echo "base_ref=\$BASE_REF"/);
  assert.match(crawlerImageJob, /if: needs\.changes\.outputs\.is_pr == 'true' && needs\.changes\.outputs\.crawler_code == 'true'/);
  assert.match(crawlerImageJob, /docker\/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e/);
  assert.match(crawlerImageJob, /docker\/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a/);
  assert.match(crawlerImageJob, /context: apps\/crawler/);
  assert.match(crawlerImageJob, /target: full/);
  assert.match(crawlerImageJob, /push: false/);
  assert.match(versionJob, /if: needs\.changes\.outputs\.is_pr == 'true'/);
  assert.match(versionJob, /gh api "repos\/\$GITHUB_REPOSITORY\/pulls\/\$PR_NUMBER"/);
  assert.match(versionJob, /scripts\/check-crawler-version\.mjs/);
  assert.match(versionJob, /\.user\.login/);
  assert.match(probeJob, /if: needs\.changes\.outputs\.is_pr == 'true'/);
  assert.match(probeJob, /BASE_REF: \$\{\{ needs\.changes\.outputs\.base_ref \}\}/);
  assert.match(requiredCiJob, /const isPr = needs\.changes\?\.outputs\?\.is_pr === "true"/);
  assert.match(requiredCiJob, /requireSuccess\("crawler-image", isPr && crawlerCode\)/);
  assert.match(requiredCiJob, /requireSuccess\("version-check", isPr && crawlerCode\)/);
  assert.match(requiredCiJob, /requireSuccess\("probe-new-boards", isPr && boardsCsv\)/);
  assert.doesNotMatch(versionJob, /github\.event_name == 'pull_request'/);
  assert.doesNotMatch(probeJob, /github\.event_name == 'pull_request'/);
});

test("workflow-security runs repository script tests", () => {
  assert.match(workflow, /name: Test observability rollback retention/);
  assert.match(
    workflow,
    /python3 deploy\/observability\/test_prune_rollbacks\.py/,
  );
  assert.match(workflow, /node --test/);
  assert.match(workflow, /scripts\/ci-workflow\.test\.mjs/);
  assert.match(workflow, /scripts\/crawler-version\.test\.mjs/);
  assert.match(workflow, /scripts\/crawler-host-hygiene\.test\.mjs/);
  assert.match(workflow, /scripts\/docs-index\.test\.mjs/);
  assert.match(workflow, /scripts\/dealroom-company-requests\.test\.mjs/);
});

test("crawler deploys derive immutable versions for unchanged releases", () => {
  assert.match(deployCrawlerWorkflow, /'!apps\/crawler\/ws-package\/\*\*'/);
  assert.match(
    deployCrawlerWorkflow,
    /'\.github\/workflows\/deploy-crawler-browser\.yml'/,
  );
  assert.match(deployCrawlerWorkflow, /fetch-depth: 0/);
  assert.match(
    deployCrawlerWorkflow,
    /BASE_SHA: \$\{\{ github\.event\.before \}\}[\s\S]*scripts\/derive-crawler-build-version\.mjs[\s\S]*--base "\$BASE_SHA"[\s\S]*--write-version apps\/crawler\/VERSION[\s\S]*--github-output "\$GITHUB_OUTPUT"/,
  );
  assert.match(
    deployCrawlerWorkflow,
    /jobseek-crawler:\$\{\{ steps\.version\.outputs\.image_tag \}\}/,
  );
  assert.match(
    deployCrawlerWorkflow,
    /CRAWLER_IMAGE_TAG: \$\{\{ needs\.build\.outputs\.image_tag \}\}/,
  );
  assert.doesNotMatch(
    deployCrawlerWorkflow,
    /steps\.version\.outputs\.version/,
  );
});

test("web build jobs use one deterministic secretless environment", () => {
  for (const jobId of ["test-web-isr", "web-smoke"]) {
    const job = jobBlock(jobId);
    assert.match(job, /uses: \.\/\.github\/actions\/setup-web-build-env/);
    assert.doesNotMatch(job, /environment: Production/);
    assert.doesNotMatch(job, /secrets\./);
  }

  for (const variable of [
    "DATABASE_URL",
    "DATABASE_URL_UNPOOLED",
    "TYPESENSE_HOST",
    "TYPESENSE_PORT",
    "TYPESENSE_PROTOCOL",
  ]) {
    assert.match(webBuildEnvAction, new RegExp(`echo '${variable}='`));
  }
  assert.match(webBuildEnvAction, /BETTER_AUTH_URL=\$APP_URL/);
  assert.match(webBuildEnvAction, /COMPANY_OG_PRERENDER_TOP_N=0/);
  assert.doesNotMatch(webBuildEnvAction, /secrets\./);
  assert.doesNotMatch(webBuildEnvAction, /postgres(?:ql)?:\/\//);
});

test("Codex deploy transport outlives the runner lock wait", () => {
  const workflowLockTimeout = deployCodexRunnerWorkflow.match(
    /^  JOBSEEK_CODEX_DEPLOY_LOCK_TIMEOUT_S: "(\d+)"$/m,
  );
  const hostLockTimeout = deployCodexRunnerHostScript.match(
    /LOCK_TIMEOUT_S="\$\{JOBSEEK_CODEX_DEPLOY_LOCK_TIMEOUT_S:-(\d+)\}"/,
  );
  const commandTimeout = deployCodexRunnerWorkflow.match(
    /^          command_timeout: (\d+[smh])$/m,
  );

  assert.ok(workflowLockTimeout, "missing workflow runner-lock timeout");
  assert.ok(hostLockTimeout, "missing host runner-lock timeout default");
  assert.ok(commandTimeout, "missing SSH command timeout");
  assert.equal(workflowLockTimeout[1], hostLockTimeout[1]);
  assert.match(
    deployCodexRunnerWorkflow,
    /envs: GITHUB_SHA,JOBSEEK_CODEX_DEPLOY_LOCK_TIMEOUT_S/,
  );
  assert.ok(
    durationSeconds(commandTimeout[1]) >= Number(workflowLockTimeout[1]) + 900,
    "SSH command timeout must include the lock wait plus 15 minutes for deployment",
  );
  assert.match(deployCodexRunnerWorkflow, /cancel-in-progress: false/);
});

test("CSV sync cannot publish configuration ahead of its crawler runtime", () => {
  assert.match(
    deployCrawlerWorkflow,
    /^concurrency:\n\s+group: crawler-production-sync\n\s+cancel-in-progress: false/m,
  );
  assert.match(
    syncDataWorkflow,
    /^concurrency:\n(?:\s+#.*\n)*\s+group: crawler-production-csv-sync\n\s+cancel-in-progress: false/m,
  );
  assert.match(
    crawlerDeployScript,
    /exec 9>\/run\/lock\/jobseek-crawler-mutation\.lock[\s\S]*uv run --no-sync crawler sync/,
  );
  assert.match(
    syncDataWorkflow,
    /\/usr\/local\/sbin\/jobseek-maintenance window[\s\S]*--operation csv-data-sync/,
  );
  assert.match(
    crawlerMaintenanceScript,
    /MUTATION_LOCK = Path\("\/run\/lock\/jobseek-crawler-mutation\.lock"\)/,
  );
  assert.match(
    syncDataWorkflow,
    /SYNC_REVISION: \$\{\{ inputs\.revision \|\| github\.sha \}\}[\s\S]*id: runtime_contract[\s\S]*--revision "\$SYNC_REVISION"[\s\S]*envs: SYNC_REVISION,SYNC_RUNTIME_CONTRACT_SHA256,SYNC_DATA_CONTRACT_SHA256,SYNC_CANDIDATE_ID,SYNC_ARCHIVE_SHA256[\s\S]*"\$SYNC_REVISION" "\$SYNC_RUNTIME_CONTRACT_SHA256"/,
  );
  assert.match(
    syncDataWorkflow,
    /name: Check out trusted CSV sync runner[\s\S]*ref: \$\{\{ github\.sha \}\}[\s\S]*scripts\/crawler-csv-sync-host\.sh[\s\S]*scripts\/derive-crawler-runtime-contract\.mjs[\s\S]*sparse-checkout-cone-mode: false/,
  );
  assert.match(
    syncDataWorkflow,
    /name: Check out revision-pinned CSV data[\s\S]*ref: \$\{\{ inputs\.revision \|\| github\.sha \}\}[\s\S]*path: csv-source/,
  );
  assert.match(
    syncDataWorkflow,
    /name: Check out revision-pinned CSV data[\s\S]*fetch-depth: 0[\s\S]*path: csv-source/,
  );
  assert.equal(
    syncDataWorkflow.match(
      /\+refs\/heads\/main:refs\/remotes\/origin\/main/g,
    )?.length,
    2,
  );
  assert.match(
    syncDataWorkflow,
    /git -C csv-source rev-parse HEAD[\s\S]*git -C csv-source merge-base --is-ancestor/,
  );
  assert.match(
    syncDataWorkflow,
    /--kind data[\s\S]*target_data_contract[\s\S]*current_data_contract[\s\S]*requested CSV snapshot is stale relative to current main/,
  );
  assert.match(
    syncDataWorkflow,
    /name: Revalidate publishable CSV snapshot[\s\S]*--kind data[\s\S]*current main CSV snapshot advanced before publication/,
  );
  assert.doesNotMatch(syncDataWorkflow, /current_runtime_contract/);
  assert.match(
    syncDataWorkflow,
    /SYNC_RUNTIME_CONTRACT_SHA256: \$\{\{ steps\.runtime_contract\.outputs\.runtime_contract_sha256 \}\}/,
  );
  assert.doesNotMatch(syncDataWorkflow, /\/home\/deploy\/\.env|CRAWLER_IMAGE_REF/);
  assert.match(syncDataWorkflow, /if \[\[ "\$before_contract" != "\$target_contract" \]\]/);
  assert.match(syncDataWorkflow, /run_sync=false/);
  const compatibilityPoll = syncDataWorkflow.indexOf("--check-runtime");
  const mutationWindow = syncDataWorkflow.indexOf(
    "/usr/local/sbin/jobseek-maintenance window",
  );
  assert.ok(compatibilityPoll >= 0 && compatibilityPoll < mutationWindow);
  assert.match(
    syncDataWorkflow,
    /if \[\[ "\$status" -eq 75 \]\][\s\S]*sleep 15[\s\S]*Crawler generation changed before sync lock; retrying/,
  );
  assert.match(
    deployCrawlerWorkflow,
    /'!apps\/crawler\/data\/\*\*'[\s\S]*'apps\/crawler\/data\/industries\.csv'[\s\S]*'apps\/crawler\/data\/occupations\.csv'[\s\S]*'apps\/crawler\/data\/seniority\.csv'[\s\S]*'apps\/crawler\/data\/technologies\.csv'/,
  );
  assert.match(crawlerCsvSyncHostScript, /\.crawler-active-release/);
  assert.match(crawlerCsvSyncHostScript, /JOBSEEK_RUNTIME_CONTRACT_SHA256/);
  assert.match(crawlerCsvSyncHostScript, /WAIT: CSV config requires[\s\S]*return 75/);
  assert.match(crawlerCsvSyncHostScript, /grep -E "\^\$\{key\}=" "\$release\/environment.env"/);
  assert.match(
    crawlerCsvSyncHostScript,
    /read_exact_value "\$generation\/environment.env" CRAWLER_IMAGE_REF/,
  );
  assert.match(syncDataWorkflow, /\/home\/deploy\/csv-candidates\/\$\{\{ steps\.candidate\.outputs\.candidate_id \}\}/);
  assert.doesNotMatch(syncDataWorkflow, /\/home\/deploy\/csv-overlay/);
  assert.match(crawlerCsvSyncHostScript, /DATA_CONTRACT_SHA256/);
  assert.match(crawlerCsvSyncHostScript, /CSV candidate archive digest mismatch/);
  assert.match(crawlerCsvSyncHostScript, /"\$ACTIVE_DATA_DIR:\/app\/data:ro"/);
  assert.match(crawlerCsvSyncHostScript, /live crawler environment drifted from committed release/);
  assert.match(crawlerCsvSyncHostScript, /RECOVERY_ACTION/);
  assert.match(crawlerCsvSyncHostScript, /trap cleanup EXIT/);
  assert.match(crawlerCsvSyncHostScript, /docker rm -f "\$NAME"/);
});

test("CSV sync history and main refresh survive a shallow push checkout", () => {
  const deployJob = deployCrawlerWorkflow.slice(
    deployCrawlerWorkflow.indexOf("\n  deploy:"),
    deployCrawlerWorkflow.indexOf("\n  promote:"),
  );
  assert.match(
    deployJob,
    /actions\/checkout@[0-9a-f]+[^\n]*\n\s+with:\n\s+fetch-depth: 0/,
  );
  assert.match(
    deployJob,
    /git rev-parse "\$\{PREVIOUS_REVISION\}\^\{commit\}"[\s\S]*git merge-base --is-ancestor "\$PREVIOUS_REVISION" "\$GITHUB_SHA"[\s\S]*git archive "\$PREVIOUS_REVISION"/,
  );
  const dir = mkdtempSync(join(tmpdir(), "csv-sync-history-"));
  const source = join(dir, "source");
  const checkout = join(dir, "checkout");
  const git = (cwd, args, expectedStatus = 0) => {
    const result = spawnSync("git", args, { cwd, encoding: "utf8" });
    assert.equal(
      result.status,
      expectedStatus,
      `${args.join(" ")}\n${result.stderr}`,
    );
    return result.stdout.trim();
  };
  try {
    mkdirSync(join(source, "apps/crawler/src"), { recursive: true });
    mkdirSync(join(source, "apps/crawler/data"), { recursive: true });
    git(source, ["init", "--initial-branch", "main"]);
    git(source, ["config", "user.email", "ci@example.invalid"]);
    git(source, ["config", "user.name", "CI Test"]);
    writeFileSync(join(source, "apps/crawler/src/runtime.py"), "VERSION = 1\n");
    writeFileSync(join(source, "apps/crawler/data/boards.csv"), "slug\nfirst\n");
    git(source, ["add", "."]);
    git(source, ["commit", "-m", "initial"]);
    const before = git(source, ["rev-parse", "HEAD"]);

    writeFileSync(join(source, "apps/crawler/src/runtime.py"), "VERSION = 2\n");
    git(source, ["add", "."]);
    git(source, ["commit", "-m", "runtime push"]);
    const target = git(source, ["rev-parse", "HEAD"]);

    git(dir, [
      "clone",
      "--depth",
      "1",
      "--branch",
      "main",
      `file://${source}`,
      checkout,
    ]);
    git(checkout, ["cat-file", "-e", `${before}^{commit}`], 128);
    git(checkout, ["archive", "--format=tar", before, "apps/crawler/data"], 128);

    // The deploy checkout's fetch-depth:0 must make github.event.before
    // available to both ancestry validation and the rollback archive build.
    git(checkout, ["fetch", "--unshallow", "origin"]);
    git(checkout, ["cat-file", "-e", `${before}^{commit}`]);
    assert.equal(git(checkout, ["rev-parse", "HEAD"]), target);
    assert.equal(git(checkout, ["rev-parse", `${before}^{commit}`]), before);
    git(checkout, ["merge-base", "--is-ancestor", before, target]);
    git(checkout, ["archive", "--format=tar", before, "apps/crawler/data"]);

    writeFileSync(
      join(source, "apps/crawler/data/boards.csv"),
      "slug\nfirst\nsecond\n",
    );
    git(source, ["add", "."]);
    git(source, ["commit", "-m", "data advance"]);
    const advancedMain = git(source, ["rev-parse", "HEAD"]);
    assert.equal(git(checkout, ["rev-parse", "origin/main"]), target);

    // Both workflow validations use this explicit destination refspec, so a
    // main advance cannot remain hidden in FETCH_HEAD.
    git(checkout, [
      "fetch",
      "--no-tags",
      "origin",
      "+refs/heads/main:refs/remotes/origin/main",
    ]);
    assert.equal(git(checkout, ["rev-parse", "origin/main"]), advancedMain);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("Codex deploy reserves the next runner-lock handoff", () => {
  const pauseCall = deployCodexRunnerHostScript.indexOf(
    "  pause_timer_activations\n",
  );
  const lockWait = deployCodexRunnerHostScript.indexOf(
    '  if ! flock -w "${LOCK_TIMEOUT_S}" 9; then',
  );

  assert.ok(pauseCall >= 0, "deployment must pause timer activations");
  assert.ok(lockWait > pauseCall, "timers must pause before waiting for the lock");
  assert.match(
    deployCodexRunnerHostScript,
    /systemctl is-active --quiet "\$\{timer\}"[\s\S]*systemctl stop "\$\{ACTIVE_TIMERS_BEFORE_DEPLOY\[@\]\}"/,
  );
  assert.match(
    deployCodexRunnerHostScript,
    /trap restore_timers_on_exit EXIT/,
  );
  assert.match(
    deployCodexRunnerHostScript,
    /restore_candidates=\("\$\{TIMERS\[@\]\}"\)[\s\S]*restore_candidates=\("\$\{ACTIVE_TIMERS_BEFORE_DEPLOY\[@\]\}"\)/,
  );
  assert.match(
    deployCodexRunnerHostScript,
    /for timer in "\$\{restore_candidates\[@\]\}"; do[\s\S]*jobseek-codex-daily-annotations\.timer[\s\S]*LABELLER_CONTRACT_VERIFIED[\s\S]*safe_restore\+=\("\$\{timer\}"\)/,
  );
  assert.match(
    deployCodexRunnerHostScript,
    /if \(\(\$\{#safe_restore\[@\]\} > 0\)\); then[\s\S]*systemctl start "\$\{safe_restore\[@\]\}"/,
  );
  assert.doesNotMatch(
    deployCodexRunnerHostScript,
    /systemctl start "\$\{ACTIVE_TIMERS_BEFORE_DEPLOY\[@\]\}"/,
    "deployment must restore only timers that satisfy their runtime contracts",
  );
  assert.doesNotMatch(
    deployCodexRunnerHostScript,
    /systemctl stop "\$\{UNITS\[@\]\}"/,
    "deployment must not interrupt an active service",
  );
});

test("Codex deploy persists Docker lifecycle evidence before daily review", () => {
  assert.match(
    deployCodexRunnerHostScript,
    /jobseek-codex-docker-lifecycle\.service/,
  );
  assert.match(
    deployCodexRunnerHostScript,
    /scripts\/codex-docker-lifecycle-watch\.py/,
  );
  assert.match(
    deployCodexRunnerHostScript,
    /systemctl enable "\$\{ALWAYS_ON_SERVICES\[@\]\}"/,
  );
  assert.match(
    deployCodexRunnerHostScript,
    /verify_entrypoints\n  start_always_on_services/,
  );
  assert.match(
    deployCodexRunnerHostScript,
    /systemctl restart "\$\{ALWAYS_ON_SERVICES\[@\]\}"[\s\S]*systemctl is-active --quiet "\$\{service\}"/,
  );
});

test("Codex deploy installs the maintenance contract without granting runner Docker access", () => {
  assert.match(
    deployCodexRunnerHostScript,
    /install_maintenance_contract[\s\S]*jobseek_maintenance_provenance\.py[\s\S]*jobseek-maintenance\.py/,
  );
  assert.match(
    deployCodexRunnerHostScript,
    /python3 \/usr\/local\/sbin\/jobseek-maintenance --self-test/,
  );
  assert.match(
    deployCodexRunnerWorkflow,
    /test ! -w \/var\/run\/docker\.sock/,
  );
  assert.match(
    deployCodexRunnerWorkflow,
    /runuser -u codex-runner -- docker ps/,
  );
  assert.match(
    deployCodexRunnerWorkflow,
    /codex-runner unexpectedly has Docker access/,
  );
});

test("maintenance wrapper self-test covers the cross-owner lock path", () => {
  const result = spawnSync(
    "python3",
    ["scripts/jobseek-maintenance.py", "--self-test"],
    {
      cwd: process.cwd(),
      encoding: "utf8",
    },
  );

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /maintenance provenance self-test passed/);
});

test("Codex deploy restores prior timer state after failure", () => {
  const dir = mkdtempSync(join(tmpdir(), "codex-deploy-timers-"));
  const log = join(dir, "systemctl.log");
  const result = spawnSync(
    "bash",
    [
      "-c",
      `set -euo pipefail
source scripts/deploy-codex-runner-host.sh
TIMERS=(alpha.timer jobseek-codex-daily-annotations.timer beta.timer)
START_TIMERS=0
LABELLER_CONTRACT_VERIFIED=0
systemctl() {
  printf '%s\\n' "$*" >> "$MOCK_SYSTEMCTL_LOG"
  if [[ "$1" == "is-active" ]]; then
    [[ "$3" == "alpha.timer" || "$3" == "jobseek-codex-daily-annotations.timer" ]]
    return
  fi
  return 0
}
pause_timer_activations
exit 23
`,
    ],
    {
      cwd: process.cwd(),
      env: { ...process.env, MOCK_SYSTEMCTL_LOG: log },
      encoding: "utf8",
    },
  );
  const calls = readFileSync(log, "utf8");
  rmSync(dir, { recursive: true, force: true });

  assert.equal(result.status, 23, result.stderr);
  assert.match(calls, /^is-active --quiet alpha\.timer$/m);
  assert.match(
    calls,
    /^is-active --quiet jobseek-codex-daily-annotations\.timer$/m,
  );
  assert.match(calls, /^is-active --quiet beta\.timer$/m);
  assert.match(
    calls,
    /^stop alpha\.timer jobseek-codex-daily-annotations\.timer$/m,
  );
  assert.match(calls, /^start alpha\.timer$/m);
  assert.doesNotMatch(
    calls,
    /^start .*jobseek-codex-daily-annotations\.timer/m,
  );
  assert.doesNotMatch(calls, /^start beta\.timer$/m);
});

test("scheduled maintenance always reports host hygiene independently", () => {
  assert.match(crawlerHostHygieneScript, /from datetime import datetime, timezone/);
  assert.match(crawlerHostHygieneScript, /UTC = timezone\.utc/);
  assert.doesNotMatch(crawlerHostHygieneScript, /from datetime import UTC/);
  assert.match(
    crawlerScheduledMaintenanceWorkflow,
    /docker run --rm[\s\S]*\|\| maintenance_status=\$\?[\s\S]*crawler-host-hygiene\.py" \|\| hygiene_status=\$\?/,
  );
  assert.match(
    crawlerScheduledMaintenanceWorkflow,
    /maintenance_status != 0 \|\| hygiene_status != 0/,
  );
});

test("scheduled maintenance one-offs carry exact validated provenance labels", () => {
  for (const label of [
    "com.docker.compose.project=deploy",
    "com.docker.compose.oneoff=True",
    "jobseek.maintenance.operation=${TASK}",
    "jobseek.maintenance.issue=2630",
    "jobseek.maintenance.revision=${GITHUB_SHA}",
    "jobseek.maintenance.budget-seconds=${operation_budget}",
  ]) {
    assert.ok(
      crawlerScheduledMaintenanceWorkflow.includes(label),
      `missing maintenance label ${label}`,
    );
  }
  assert.match(
    crawlerScheduledMaintenanceWorkflow,
    /envs: GITHUB_SHA,EXPECTED_CRAWLER_REVISION/,
  );
  assert.match(crawlerScheduledMaintenanceWorkflow, /operation_budget=7200/);
  assert.match(
    crawlerScheduledMaintenanceWorkflow,
    /if \[\[ "\$TASK" == backfill-typesense \|\| "\$TASK" == verify-typesense-taxonomies \]\]; then[\s\S]*operation_budget=14400/,
  );
  assert.match(
    crawlerScheduledMaintenanceWorkflow,
    /timeout --foreground --signal=TERM --kill-after=90s "\$operation_budget" docker run --rm/,
  );
  assert.match(crawlerScheduledMaintenanceWorkflow, /command_timeout: 8h/);
});

test("taxonomy verification dispatch is exact-revision and verification-only", () => {
  assert.match(
    crawlerScheduledMaintenanceWorkflow,
    /options:[\s\S]*- refresh-typesense[\s\S]*- backfill-typesense[\s\S]*- verify-typesense-taxonomies/,
  );
  assert.match(
    crawlerScheduledMaintenanceWorkflow,
    /if \[\[ "\$task" == backfill-typesense \|\| "\$task" == verify-typesense-taxonomies \]\]; then[\s\S]*\^\[0-9a-f\]\{40\}\$/,
  );
  assert.match(
    crawlerScheduledMaintenanceWorkflow,
    /if \[\[ "\$TASK" == backfill-typesense \|\| "\$TASK" == verify-typesense-taxonomies \]\]; then[\s\S]*Live crawler revision does not match the requested deployment[\s\S]*Expected exactly one live exporter container[\s\S]*Live exporter still has a relational mirror credential/,
  );
  const verificationBranch = crawlerScheduledMaintenanceWorkflow.match(
    /elif \[\[ "\$TASK" == verify-typesense-taxonomies \]\]; then[\s\S]*?^              fi$/m,
  );
  assert.ok(verificationBranch);
  assert.match(
    verificationBranch[0],
    /operation_command=\(uv run --no-sync crawler verify-typesense-taxonomies\)/,
  );
  assert.doesNotMatch(verificationBranch[0], /crawler backfill-typesense/);
  assert.doesNotMatch(verificationBranch[0], /crawler reconcile/);
  assert.match(
    crawlerScheduledMaintenanceWorkflow,
    /\^crawler-\(backfill-typesense\|refresh-typesense\|verify-typesense-taxonomies\)-/,
  );
});

test("recurring crawler operations use the bounded maintenance wrapper", () => {
  for (const [source, operation, issue, budget, mode, lockTimeout, timeout] of [
    [
      syncDataWorkflow,
      "csv-data-sync",
      "2623",
      "1800",
      "window",
      "1500",
      "2h",
    ],
    [
      refreshCurrencyRatesWorkflow,
      "refresh-currency-rates",
      "3576",
      "600",
      "oneoff",
      "600",
      "30m",
    ],
  ]) {
    assert.match(
      source,
      new RegExp(`/usr/local/sbin/jobseek-maintenance ${mode}`),
    );
    assert.ok(source.includes(`--operation ${operation}`));
    assert.ok(source.includes(`--issue ${issue}`));
    assert.ok(source.includes(`--budget-seconds ${budget}`));
    assert.ok(source.includes(`--lock-timeout-seconds ${lockTimeout}`));
    if (operation === "csv-data-sync") {
      assert.ok(source.includes('--revision "$SYNC_REVISION"'));
      assert.match(source, /envs: SYNC_REVISION/);
    } else {
      assert.ok(source.includes('--revision "$GITHUB_SHA"'));
      assert.match(source, /envs: GITHUB_SHA/);
    }
    assert.ok(source.includes(`command_timeout: ${timeout}`));
  }
  assert.match(
    crawlerCsvSyncHostScript,
    /docker run --rm[\s\S]*--name "\$NAME"/,
  );
  assert.match(
    refreshCurrencyRatesWorkflow,
    /docker run --rm[\s\S]*--name "\$NAME"/,
  );
});

test("maybe-auto-merge wakes without manual retries", () => {
  const job = workflowJobBlock(maybeAutoMergeWorkflow, "label-and-merge");
  assert.match(maybeAutoMergeWorkflow, /workflow_run:\n    workflows: \["CI", "CodeQL"\]/);
  assert.match(maybeAutoMergeWorkflow, /schedule:\n    - cron: "\*\/15 \* \* \* \*"/);
  assert.match(maybeAutoMergeWorkflow, /workflow_dispatch:/);
  assert.match(job, /name: Select PRs/);
  assert.match(job, /select_open_company_prs\(\)/);
  assert.match(job, /\$branch" == "\$default_branch"/);
  assert.match(job, /\$branch" == "\$default_branch"[\s\S]*select_open_company_prs >> "\$prs_file"/);
  assert.match(job, /name: Label, rebase, and merge/);
  assert.match(job, /maybe-auto-merge-pr\.sh/);
});

test("maybe-auto-merge script skips image PRs and retries pending merges", () => {
  assert.match(maybeAutoMergeScript, /apps\/crawler\/data\/images\//);
  assert.match(maybeAutoMergeScript, /upload-company-images will handle it/);
  assert.match(maybeAutoMergeScript, /label-pr\.sh/);
  assert.match(maybeAutoMergeScript, /git rebase origin\/main/);
  assert.match(maybeAutoMergeScript, /merge_company_csv_rebase\.py/);
  assert.doesNotMatch(maybeAutoMergeScript, /grep -qxF/);
  assert.match(maybeAutoMergeScript, /dispatch-pr-checks\.sh/);
  assert.match(maybeAutoMergeScript, /required_ci_state\(\)/);
  assert.match(maybeAutoMergeScript, /wait_for_required_ci\(\)/);
  assert.match(maybeAutoMergeScript, /Required CI is successful/);
  assert.match(maybeAutoMergeScript, /gh pr merge "\$PR" --repo "\$REPO" --rebase/);
  assert.match(
    maybeAutoMergeScript,
    /gh pr merge "\$PR" --repo "\$REPO" --rebase[\s\S]*dispatch-company-production-sync\.sh[\s\S]*close-linked-company-request-issues\.sh/,
  );
  assert.match(maybeAutoMergeScript, /scheduled\/workflow_run retries will revisit it/);
});

test("company auto-merges prewarm before exact-revision production sync without deploying web", () => {
  for (const source of [maybeAutoMergeWorkflow, uploadCompanyImagesWorkflow]) {
    assert.match(
      source,
      /cp \.github\/scripts\/dispatch-company-production-sync\.sh "\$RUNNER_TEMP\/trusted-scripts\/dispatch-company-production-sync\.sh"/,
    );
  }

  assert.match(
    uploadCompanyImagesWorkflow,
    /name: Dispatch production CSV sync[\s\S]*steps\.merge\.outputs\.merged == 'true'[\s\S]*dispatch-company-production-sync\.sh/,
  );
  assert.match(
    dispatchCompanyProductionSyncScript,
    /gh workflow run prewarm-company-og-cache\.yml[\s\S]*gh run watch "\$prewarm_run_id"[\s\S]*gh workflow run sync-data\.yml[\s\S]*-f revision="\$prewarm_sha"/,
  );
  assert.doesNotMatch(
    dispatchCompanyProductionSyncScript,
    /deploy-web-production\.yml/,
  );

  for (const fixture of [
    { defaultBranch: "main", includeDefaultBranch: false },
    { defaultBranch: "release", includeDefaultBranch: true },
  ]) {
    const result = runDispatchCompanyProductionSync(fixture);
    assert.equal(result.status, 0, result.stderr);
    const expectedBranch = fixture.includeDefaultBranch
      ? fixture.defaultBranch
      : "main";
    const calls = result.calls.trim().split("\n");
    assert.deepEqual(
      [calls[0], calls[2], calls[3]],
      [
        `workflow run prewarm-company-og-cache.yml --repo colophon-group/jobseek --ref ${expectedBranch} -f concurrency=4`,
        "run watch 4242 --repo colophon-group/jobseek --exit-status",
        `workflow run sync-data.yml --repo colophon-group/jobseek --ref ${expectedBranch} -f revision=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`,
      ],
    );
    assert.match(
      calls[1],
      new RegExp(
        `^run list --repo colophon-group/jobseek --workflow prewarm-company-og-cache\\.yml --branch ${expectedBranch} --event workflow_dispatch`,
      ),
    );
    assert.doesNotMatch(result.calls, /deploy-web-production\.yml/);
  }

  const failedPrewarm = runDispatchCompanyProductionSync({ prewarmWatchStatus: 1 });
  assert.equal(failedPrewarm.status, 1);
  assert.doesNotMatch(failedPrewarm.calls, /workflow run deploy-web-production\.yml/);
  assert.doesNotMatch(failedPrewarm.calls, /workflow run sync-data\.yml/);

});

test("bot-authored company branch updates dispatch path-aware CI", () => {
  assert.match(dispatchPrChecksScript, /gh workflow run ci\.yml --repo "\$REPO" --ref "\$branch" -f "pr=\$PR"/);
  assert.doesNotMatch(dispatchPrChecksScript, /codeql\.yml/);
  assert.doesNotMatch(dispatchPrChecksScript, /add-company\/\*/);
  assert.match(dispatchPrChecksScript, /"\$BRANCH" != "\$pr_branch"/);
  assert.match(dispatchPrChecksScript, /Unexpected inputs provided: \\\["pr"\\\]/);
  assert.match(dispatchPrChecksScript, /later rebase retry will dispatch CI/);

  assert.match(maybeAutoMergeWorkflow, /actions: write/);
  assert.match(maybeAutoMergeWorkflow, /dispatch-pr-checks\.sh/);

  assert.match(uploadCompanyImagesWorkflow, /actions: write/);
  assert.match(uploadCompanyImagesWorkflow, /id: image-sync/);
  assert.match(uploadCompanyImagesWorkflow, /steps\.image-sync\.outputs\.pushed == 'true'/);
  assert.match(uploadCompanyImagesWorkflow, /Dispatch checks for image commit/);
  assert.match(uploadCompanyImagesWorkflow, /Auto merge is not allowed for this repository/);
  assert.match(uploadCompanyImagesWorkflow, /maybe-auto-merge-pr\.sh/);
  assert.match(uploadCompanyImagesWorkflow, /Retry trusted auto-merge/);
  assert.match(uploadCompanyImagesWorkflow, /TRUSTED_SCRIPTS_DIR: \$\{\{ runner\.temp \}\}\/trusted-scripts/);
});

test("company image upload is PR-scoped and waits for Required CI", () => {
  assert.match(
    uploadCompanyImagesWorkflow,
    /git diff --name-only --diff-filter=ACMRT "\$trusted_ref"\.\.\.HEAD/,
  );
  assert.match(uploadCompanyImagesWorkflow, /image_args\+=\(--slug "\$slug"\)/);
  assert.match(
    uploadCompanyImagesWorkflow,
    /uv run python -m src\.image_sync "\$\{image_args\[@\]\}"/,
  );
  assert.doesNotMatch(uploadCompanyImagesWorkflow, /git add apps\/crawler\/data\//);
  assert.match(
    uploadCompanyImagesWorkflow,
    /Branch protection is unavailable; trusted retry will wait for Required CI/,
  );
  assert.doesNotMatch(
    uploadCompanyImagesWorkflow,
    /gh pr checks "\$PR" --repo "\$REPO" --watch --fail-fast \|\| true/,
  );
});

test("trusted image cleanup dispatches add-company and coding-mode PRs", () => {
  for (const branch of ["add-company/example", "fix-crawler/example-repair"]) {
    const result = runDispatchPrChecks({ branch });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.calls, /workflow run ci\.yml/);
    assert.match(result.calls, new RegExp(`--ref ${branch.replace("/", "\\/")}`));
    assert.match(result.calls, /-f pr=123/);
  }
});

test("trusted image cleanup does not dispatch forks, drafts, or closed PRs", () => {
  for (const fixture of [
    { owner: "external-contributor" },
    { isDraft: true },
    { state: "CLOSED" },
  ]) {
    const result = runDispatchPrChecks(fixture);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.calls, "");
  }
});

test("trusted image cleanup rejects an event branch that disagrees with the PR", () => {
  const result = runDispatchPrChecks({
    branch: "fix-crawler/current-head",
    requestedBranch: "fix-crawler/stale-event",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /not requested branch/);
  assert.equal(result.calls, "");
});

test("company image cleanup commits cannot absorb trusted source changes", () => {
  assert.match(
    uploadCompanyImagesWorkflow,
    /git restore --source="\$trusted_ref" --worktree -- \\\n+              apps\/crawler\/src/,
  );
  assert.doesNotMatch(
    uploadCompanyImagesWorkflow,
    /git restore --source="\$trusted_ref" --staged/,
  );
  assert.match(
    uploadCompanyImagesWorkflow,
    /git add -A -- "\$\{target_paths\[@\]\}"/,
  );
  assert.match(
    uploadCompanyImagesWorkflow,
    /if \[\[ "\$staged_path" == "apps\/crawler\/data\/images\/\$slug\/"\* \]\]/,
  );
  assert.match(uploadCompanyImagesWorkflow, /git diff --cached --name-only/);
  assert.match(
    uploadCompanyImagesWorkflow,
    /Refusing to commit paths outside the PR-scoped image update/,
  );
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

function shellAllowlist(name) {
  const match = labelPrScript.match(new RegExp(`^${name}='([^']*)'$`, "m"));
  assert.ok(match, `missing shell allowlist: ${name}`);
  return new Set(match[1].split("|"));
}

function registeredTypes(directory) {
  const types = new Set();
  for (const filename of readdirSync(directory)) {
    if (!filename.endsWith(".py")) continue;
    const source = readFileSync(join(directory, filename), "utf8");
    for (const match of source.matchAll(/\bregister\(\s*["']([^"']+)/g)) {
      types.add(match[1]);
    }
  }
  return types;
}

function netAddedCsvRows(diff) {
  const result = spawnSync("python3", [labelPrCsvDiffHelper], {
    cwd: process.cwd(),
    input: diff,
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim() ? result.stdout.trim().split("\n") : [];
}

test("company PR static type allowlists match runtime registrations", () => {
  assert.deepEqual(
    shellAllowlist("VALID_MONITOR_TYPES"),
    registeredTypes("apps/crawler/src/core/monitors"),
  );
  assert.deepEqual(
    shellAllowlist("VALID_SCRAPER_TYPES"),
    registeredTypes("apps/crawler/src/core/scrapers"),
  );
});

test("company PR workflows capture the semantic diff helper as trusted code", () => {
  for (const source of [maybeAutoMergeWorkflow, uploadCompanyImagesWorkflow]) {
    assert.match(
      source,
      /cp \.github\/scripts\/label_pr_csv_diff\.py "\$RUNNER_TEMP\/trusted-scripts\/label_pr_csv_diff\.py"/,
    );
    assert.match(
      source,
      /cp \.github\/scripts\/merge_company_csv_rebase\.py "\$RUNNER_TEMP\/trusted-scripts\/merge_company_csv_rebase\.py"/,
    );
  }
});

test("company PR classifier cancels CSV row moves", () => {
  const moved = "old,same,row";
  const added = "company,board,https://example.com,sitemap,,skip,";
  const diff = `diff --git a/apps/crawler/data/boards.csv b/apps/crawler/data/boards.csv
--- a/apps/crawler/data/boards.csv
+++ b/apps/crawler/data/boards.csv
@@ -1,2 +1,2 @@
-${moved}
+${added}
 ${moved}
@@ -10,1 +10,1 @@
-${added}
+${moved}
`;

  assert.deepEqual(netAddedCsvRows(diff), []);
});

test("company PR classifier retains only net-new CSV rows in order", () => {
  const moved = "existing,Existing,https://existing.example,,,,,,";
  const company = "new-company,New Company,https://new.example,,,,,,,";
  const board = "new-company,careers,https://new.example/jobs,comeet,{},skip,";
  const diff = `diff --git a/apps/crawler/data/companies.csv b/apps/crawler/data/companies.csv
--- a/apps/crawler/data/companies.csv
+++ b/apps/crawler/data/companies.csv
@@ -1 +1,2 @@
-${moved}
+${company}
+${moved}
diff --git a/apps/crawler/data/boards.csv b/apps/crawler/data/boards.csv
--- a/apps/crawler/data/boards.csv
+++ b/apps/crawler/data/boards.csv
@@ -1 +1 @@
+${board}
`;

  assert.deepEqual(netAddedCsvRows(diff), [company, board]);
});

test("company PR labeler auto-merges valid config despite moved historical rows", () => {
  const moved =
    "old-company,careers,https://old.example/jobs,paylocity,,paylocity,";
  const board =
    "new-company,careers,https://new.example/jobs,comeet,{},skip,";
  const company = "new-company,New Company,https://new.example,,,,,,,";
  const description = "new-company,English,German,French,Italian";
  const diff = `diff --git a/apps/crawler/data/boards.csv b/apps/crawler/data/boards.csv
--- a/apps/crawler/data/boards.csv
+++ b/apps/crawler/data/boards.csv
@@ -1 +1,2 @@
-${moved}
+${board}
+${moved}
diff --git a/apps/crawler/data/companies.csv b/apps/crawler/data/companies.csv
--- a/apps/crawler/data/companies.csv
+++ b/apps/crawler/data/companies.csv
@@ -1 +1 @@
+${company}
diff --git a/apps/crawler/data/company_descriptions.csv b/apps/crawler/data/company_descriptions.csv
--- a/apps/crawler/data/company_descriptions.csv
+++ b/apps/crawler/data/company_descriptions.csv
@@ -1 +1 @@
+${description}
`;
  const result = runCompanyPrLabeler(diff);

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Applied labels: auto-merge/);
  assert.match(result.outputs, /^labels=auto-merge$/m);
  assert.match(result.calls, /--remove-label review-code/);
  assert.match(result.calls, /--add-label auto-merge/);
});

test("CodeQL skips full analysis for non-code pull requests", () => {
  const changesJob = workflowJobBlock(codeqlWorkflow, "changes");
  assert.match(codeqlWorkflow, /pull_request:\n    branches: \[main\]\n    paths-ignore:/);
  assert.match(changesJob, /name: Detect CodeQL changes/);
  assert.match(changesJob, /id: manual-pr/);
  assert.match(changesJob, /\.github\/scripts\/classify-pr-paths\.sh/);
  assert.match(changesJob, /uses: dorny\/paths-filter@ceb8a2b8f2d89434be7ff52d3de7ec3738c5cc9d # v4\.0\.3/);
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

test("Dependabot updates and groups the pnpm workspace from its root", () => {
  const npmConfig = dependabotConfig.match(
    /  - package-ecosystem: "npm"\n([\s\S]*?)(?=\n  - package-ecosystem:)/,
  )?.[1];

  assert.ok(npmConfig, "missing npm Dependabot configuration");
  assert.match(npmConfig, /^    directory: "\/"$/m);
  assert.doesNotMatch(npmConfig, /^    directories:/m);
  assert.doesNotMatch(npmConfig, /exclude-paths:/);
  assert.match(
    npmConfig,
    /security-updates:\n        applies-to: "security-updates"\n        patterns:\n          - "\*"/,
  );
  assert.match(npmConfig, /next-react:\n        applies-to: "version-updates"/);
  assert.match(npmConfig, /test-tooling:\n        applies-to: "version-updates"/);
  assert.match(
    npmConfig,
    /workspace-dependencies:\n        applies-to: "version-updates"\n        patterns:\n          - "\*"\n        # Keep the catch-all as one reviewable workspace update\./,
  );
  assert.doesNotMatch(npmConfig, /group-by: "dependency-name"/);
});

test("the pnpm workspace has one JavaScript lockfile authority", () => {
  assert.equal(existsSync("pnpm-lock.yaml"), true);
  assert.equal(existsSync("apps/trace-viewer/package-lock.json"), false);
});

test("dependency review scopes the sharp libvips license exception", () => {
  const dependencyReviewJob = workflowJobBlock(workflow, "dependency-review");

  assert.match(
    dependencyReviewJob,
    /deny-licenses: .*LGPL-2\.0, LGPL-2\.1, LGPL-3\.0/,
  );
  for (const platform of [
    "darwin-arm64",
    "darwin-x64",
    "linux-arm",
    "linux-arm64",
    "linux-ppc64",
    "linux-riscv64",
    "linux-s390x",
    "linux-x64",
    "linuxmusl-arm64",
    "linuxmusl-x64",
  ]) {
    assert.match(
      dependencyReviewJob,
      new RegExp(`pkg:npm/@img/sharp-libvips-${platform}`),
    );
  }
  assert.doesNotMatch(dependencyReviewJob, /allow-licenses:/);
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
  assert.match(crawlerJob, /TYPESENSE_OPERATIONS_KEY: local_dev_typesense_key/);
  assert.match(crawlerJob, /REQUIRE_TYPESENSE_E2E: "true"/);
  assert.match(
    crawlerJob,
    /name: Wait for Typesense[\s\S]*curl -fsS http:\/\/localhost:8108\/health/,
  );
  assert.match(crawlerJob, /uv run python \.\.\/\.\.\/scripts\/typesense-setup\.py --force/);
  assert.match(crawlerJob, /uv run pytest tests\/e2e\/test_typesense_indexing\.py -v/);
});

test("Typesense credentials are separated by consumer and host promotion is manual", () => {
  assert.match(
    deployTypesenseHostWorkflow,
    /pull_request:\n    branches: \[main\]\n    paths:/,
  );
  assert.match(
    deployCrawlerWorkflow,
    /TYPESENSE_OPERATIONS_KEY: \$\{\{ secrets\.TYPESENSE_OPERATIONS_KEY \}\}/,
  );
  assert.doesNotMatch(deployCrawlerWorkflow, /TYPESENSE_ADMIN_KEY/);
  assert.match(
    deployDataBackupsWorkflow,
    /JOBSEEK_TYPESENSE_BACKUP_KEY: \$\{\{ matrix\.service == 'typesense' && secrets\.TYPESENSE_BACKUP_KEY \|\| '' \}\}/,
  );
  assert.match(
    deployTypesenseHostWorkflow,
    /TYPESENSE_BOOTSTRAP_KEY: \$\{\{ secrets\.TYPESENSE_BOOTSTRAP_KEY \}\}/,
  );
  assert.match(
    deployTypesenseHostWorkflow,
    /CLOUDFLARE_TUNNEL_TOKEN: \$\{\{ secrets\.CLOUDFLARE_TUNNEL_TOKEN \}\}/,
  );
  assert.doesNotMatch(
    deployTypesenseHostWorkflow,
    /envs: [^\n]*TYPESENSE_BOOTSTRAP_KEY[^\n]*CLOUDFLARE_TUNNEL_TOKEN|envs: [^\n]*CLOUDFLARE_TUNNEL_TOKEN[^\n]*TYPESENSE_BOOTSTRAP_KEY/,
  );
  assert.match(
    deployTypesenseHostWorkflow,
    /deploy:\n    if: github\.event_name == 'workflow_dispatch'/,
  );
  assert.match(
    deployTypesenseHostWorkflow,
    /--config=\/run\/secrets\/typesense-server\.ini/,
  );
  assert.match(
    deployTypesenseHostWorkflow,
    /-p 127\.0\.0\.1:18108:8108[\s\S]*http:\/\/127\.0\.0\.1:18108\/health/,
  );
  assert.match(deployTypesenseHostWorkflow, /echo '\[server\]'/);
  assert.match(
    deployTypesenseHostWorkflow,
    /--ulimit nofile=65536:65536[\s\S]*--log-opt max-size=50m[\s\S]*--log-opt max-file=3/,
  );
  assert.match(
    deployTypesenseHostWorkflow,
    /\.State\.Status[\s\S]*docker logs "\$container"[\s\S]*docker inspect "\$container"/,
  );
  assert.match(
    deployTypesenseHostWorkflow,
    /cleanup\(\)[\s\S]*docker rm -f "\$container"[\s\S]*sudo rm -rf -- "\$root"/,
  );
  assert.doesNotMatch(
    deployTypesenseHostWorkflow,
    /--api-key[= ]\$\{\{/,
  );
});

test("broad CI test jobs exclude service-backed Typesense E2E suites", () => {
  const webJob = jobBlock("test-web");
  assert.match(
    webJob,
    /pnpm --filter @jobseek\/web exec vitest run[\s\S]*--exclude src\/lib\/search\/__tests__\/typesense\.e2e\.test\.ts/,
  );
  assert.match(webJob, /pnpm --filter @jobseek\/trace-viewer test/);
  assert.match(webJob, /pnpm --filter @jobseek\/trace-viewer build/);

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

test("Required CI gates PostgreSQL reconciliation and sampling E2E", () => {
  const postgresJob = jobBlock("test-crawler-postgres-cdc-e2e");

  assert.match(workflow, /needs:[\s\S]*- test-crawler-postgres-cdc-e2e/);
  assert.match(workflow, /"test-crawler-postgres-cdc-e2e"/);
  assert.match(
    postgresJob,
    /uv run pytest[\s\S]*tests\/e2e\/test_postgres_cdc_commit_order\.py/,
  );
  assert.match(
    postgresJob,
    /tests\/e2e\/test_labeller_sampling_plan\.py[\s\S]*-v/,
  );
});

test("CI setup-uv steps cache uv downloads by crawler lockfile", () => {
  const blocks = setupUvBlocks(workflow);
  assert.ok(blocks.length > 0, "CI should use setup-uv");

  for (const block of blocks) {
    assert.match(block, /enable-cache: true/);
    assert.match(block, /prune-cache: true/);
    assert.match(block, /cache-dependency-glob: "apps\/crawler\/uv\.lock"/);
  }
});

test("pull_request_target image uploads disable the uv cache", () => {
  const blocks = setupUvBlocks(uploadCompanyImagesWorkflow);
  assert.ok(blocks.length > 0, "upload-company-images should use setup-uv");

  for (const block of blocks) {
    assert.match(block, /enable-cache: false/);
    assert.doesNotMatch(block, /prune-cache|cache-dependency-glob/);
  }
});

test("MCP publish workflow caches the pnpm store", () => {
  assert.match(
    publishMcpServerWorkflow,
    /pnpm\/action-setup@0977fd99725f1db4007ccb2928dbb4e90d06cc86 # v6\.0\.10[\s\S]*actions\/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v6/,
  );
  assert.match(publishMcpServerWorkflow, /cache: pnpm/);
  assert.match(publishMcpServerWorkflow, /cache-dependency-path: pnpm-lock\.yaml/);
});

test("MCP publish workflow uses npm trusted publishing", () => {
  assert.match(publishMcpServerWorkflow, /id-token: write/);
  assert.match(
    publishMcpServerWorkflow,
    /npm install --global npm@11\.19\.0/,
  );
  assert.match(publishMcpServerWorkflow, /npm publish --access public/);
  assert.doesNotMatch(publishMcpServerWorkflow, /NPM_TOKEN|NODE_AUTH_TOKEN/);
  assert.doesNotMatch(
    publishMcpServerWorkflow,
    /npm view @jseek\/mcp-server version[^\n]*\|\|/,
  );
});
