import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, chmodSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const workflow = readFileSync(".github/workflows/ci.yml", "utf8");
function classify(paths) {
  const directory = mkdtempSync(join(tmpdir(), "company-reference-ci-"));
  try {
    const gh = join(directory, "gh");
    writeFileSync(gh, '#!/usr/bin/env node\nprocess.stdout.write(process.argv.join(" ").includes("/files") ? process.env.FIXTURE_PATHS : "main\\n");\n');
    chmodSync(gh, 0o755);
    const result = spawnSync("bash", [".github/scripts/classify-pr-paths.sh"], {
      encoding: "utf8", env: { ...process.env, PATH: `${directory}:${process.env.PATH}`, GH_TOKEN: "fixture", REPO: "fixture/jobseek", PR: "1", FIXTURE_PATHS: paths.join("\n"), GITHUB_OUTPUT: "" },
    });
    assert.equal(result.status, 0, result.stderr);
    return Object.fromEntries(result.stdout.trim().split("\n").map(line => line.split("=")));
  } finally { rmSync(directory, { recursive: true, force: true }); }
}

for (const path of ["apps/web/src/lib/services/watchlists.ts", "apps/web/drizzle/0100_company_references.sql", "apps/web/drizzle/0101_company_reference_selection_contract.sql", "apps/crawler/src/core/sync.py", "apps/crawler/go/typesense-exporter/internal/export/company.go", "apps/crawler/data/companies.csv", ".github/workflows/ci.yml", ".github/workflows/deploy-web-production.yml", ".github/scripts/classify-pr-paths.sh", "scripts/company-reference-ci.test.mjs", "scripts/jobseek-data-backup.py", "deploy/backups/web-postgresql/restore-drill.sh", "deploy/backups/web-postgresql/company-reference-rehearsal.json", "apps/crawler/tests/test_company_reference_rehearsal.py", "apps/crawler/tests/test_web_postgresql_company_reference_backup.py"]) {
  test(`company reference contract is required for ${path}`, () => {
    assert.equal(classify([path]).company_reference, "true");
  });
}

test("data-only classification cannot skip first-use persistence", () => {
  const result = classify(["apps/crawler/data/companies.csv"]);
  assert.equal(result.code, "false");
  assert.equal(result.company_reference, "true");
  assert.match(workflow, /if: needs\.changes\.outputs\.company_reference == 'true'/);
  assert.match(workflow, /requireSuccess\("test-company-reference", companyReference\)/);
  assert.match(workflow, /needs:[\s\S]*?\n      - test-company-reference/);
});

test("both normal and manual dispatch propagate boundary classification", () => {
  assert.match(workflow, /company_reference: \$\{\{ steps\.manual-default\.outputs\.company_reference \|\| steps\.manual-pr\.outputs\.company_reference \|\| steps\.filter\.outputs\.company_reference \}\}/);
  assert.match(workflow, /echo "company_reference=true"/);
  for (const prefix of ["apps/web/**", "apps/crawler/src/**", "apps/crawler/go/typesense-exporter/**", "apps/crawler/data/companies.csv", ".github/**"]) assert.ok(workflow.includes(prefix));
});

test("required job executes real PostgreSQL production services and authenticated browser contract", () => {
  const job = workflow.split("  test-company-reference:\n")[1]?.split("\n  test-web-typesense-e2e:")[0];
  assert.ok(job);
  assert.match(job, /postgres:17-alpine@sha256:/);
  assert.match(job, /test:company-reference\n/);
  assert.match(job, /test:company-reference:browser/);
  assert.match(job, /playwright install --with-deps chromium/);
  assert.ok(job.indexOf("playwright install") < job.indexOf("test:company-reference\n"));
  assert.match(job, /COMPANY_REFERENCE_TEST_WRITE_MODE: reference/);
});

test("bridge promotion requires a nonoptional authenticated first-use canary", () => {
  const deploy = readFileSync(".github/workflows/deploy-web-production.yml", "utf8");
  const canary = deploy.indexOf("      - name: Prove first-use company selection before promotion");
  const promotion = deploy.indexOf("      - name: Promote only if this SHA is still main");
  assert.ok(canary > 0 && canary < promotion);
  const step = deploy.slice(canary, deploy.indexOf("\n      - name:", canary + 1));
  assert.doesNotMatch(step, /continue-on-error|CANARY_ENABLED|CANARY_REQUIRED/);
  assert.match(step, /COMPANY_REFERENCE_CANARY_USER_ID: \$\{\{ secrets\./);
  assert.match(step, /COMPANY_REFERENCE_CANARY_PASSWORD: \$\{\{ secrets\./);
  assert.match(step, /scripts\/verify-company-reference-staged\.ts/);
});

test("promotion success requires the same lifecycle through the exact public alias", () => {
  const deploy = readFileSync(".github/workflows/deploy-web-production.yml", "utf8");
  const ownership = deploy.indexOf("      - name: Verify the promoted deployment owns production");
  const live = deploy.indexOf("      - name: Prove the promoted company lifecycle through the public alias");
  const next = deploy.indexOf("      - name: Submit published blog URLs to IndexNow", live);
  assert.ok(ownership >= 0 && live > ownership && next > live);
  const step = deploy.slice(live, next);
  assert.match(step, /if: steps\.promote\.outputs\.promoted == 'true'/);
  assert.match(step, /COMPANY_REFERENCE_CANARY_TARGET: production/);
  assert.match(step, /DEPLOYMENT_URL: https:\/\/jseek\.co/);
  assert.match(step, /EXPECTED_SHA: \$\{\{ steps\.paths\.outputs\.current_sha \}\}/);
  assert.match(step, /EXPECTED_DEPLOYMENT_ID: \$\{\{ steps\.stage\.outputs\.id \}\}/);
  assert.match(step, /EXPECTED_DEPLOYMENT_URL: \$\{\{ steps\.stage\.outputs\.url \}\}/);
  assert.match(step, /COMPANY_REFERENCE_CANARY_PASSWORD: \$\{\{ secrets\./);
  assert.match(step, /scripts\/verify-company-reference-staged\.ts/);
  assert.doesNotMatch(step, /continue-on-error|VERCEL_AUTOMATION_BYPASS|CANARY_ENABLED|CANARY_REQUIRED/);
});

test('remote lifecycle boundaries let the shared IP burst budget settle without retrying actions', () => {
  const workflow = readFileSync('.github/workflows/deploy-web-production.yml', 'utf8');
  const stagedPause = workflow.indexOf('- name: Let the public-read burst window settle before staged lifecycle');
  const staged = workflow.indexOf('- name: Prove first-use company selection before promotion');
  const publicPause = workflow.indexOf('- name: Let the public-read burst window settle before public lifecycle');
  const publicCanary = workflow.indexOf('- name: Prove the promoted company lifecycle through the public alias');
  assert.ok(stagedPause >= 0 && stagedPause < staged && publicPause > staged && publicPause < publicCanary);
  assert.match(workflow.slice(stagedPause, staged), /if: steps\.paths\.outputs\.deploy == 'true'[\s\S]*run: sleep 65/);
  assert.match(workflow.slice(publicPause, publicCanary), /if: steps\.promote\.outputs\.promoted == 'true'[\s\S]*run: sleep 65/);
});
