import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  classifyVercelWebChanges,
  isVercelWebInput,
} from "../.github/scripts/classify-vercel-web-change.mjs";

test("deploys web runtime and every current workspace input", () => {
  for (const path of [
    "apps/web/app/page.tsx",
    "apps/web/vercel.json",
    "packages/mcp-server/src/handler.ts",
    "patches/next@16.2.11.patch",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "turbo.json",
    ".github/workflows/deploy-web-production.yml",
    ".github/scripts/classify-vercel-web-change.mjs",
  ]) {
    assert.equal(isVercelWebInput(path), true, path);
  }
});

test("does not redeploy for crawler, company-data, docs, or ops changes", () => {
  const result = classifyVercelWebChanges([
    "apps/crawler/data/companies.csv",
    "apps/crawler/data/company_descriptions.csv",
    "apps/crawler/src/core/monitors/workday.py",
    ".github/workflows/sync-data.yml",
    "docs/01-agent-workflow.md",
  ]);
  assert.deepEqual(result, { deploy: false, relevant: [] });
});

test("Vercel Git integration is disabled only for main", () => {
  const config = JSON.parse(readFileSync("apps/web/vercel.json", "utf8"));
  assert.deepEqual(config.git, { deploymentEnabled: { main: false } });
});

test("production workflow stages, verifies, then promotes exact main", () => {
  const workflow = readFileSync(
    ".github/workflows/deploy-web-production.yml",
    "utf8",
  );
  assert.match(workflow, /--prod --skip-domain --archive=tgz/);
  assert.match(workflow, /v13\/deployments\/\$PRODUCTION_ALIAS/);
  assert.match(workflow, /production_sha.*current_sha/);
  assert.match(workflow, /current_main=.*commits\/main/);
  assert.match(workflow, /vercel@55\.0\.0 promote/);
  assert.match(
    workflow,
    /VERCEL_TOKEN: \$\{\{ secrets\.VERCEL_TOKEN \}\}/,
  );
  const curlLines = workflow
    .split("\n")
    .filter((line) => line.includes("vercel@55.0.0") && line.includes("curl"));
  assert.equal(curlLines.length, 2);
  for (const line of curlLines) {
    assert.doesNotMatch(line, /--token/);
  }
  assert.equal(
    [...workflow.matchAll(/--output \/dev\/null --write-out '%\{http_code\}'/g)]
      .length,
    2,
  );
  assert.doesNotMatch(workflow, /--(?:output|write-out)=/);
  assert.doesNotMatch(workflow, /--cwd=apps\/web/);
  assert.doesNotMatch(workflow, /--git-branch/);
  assert.match(workflow, /environment: Production/);
  assert.doesNotMatch(workflow, /environment:\n\s+name: Production\n\s+url:/);
  assert.doesNotMatch(workflow, /pull_request:/);
});
