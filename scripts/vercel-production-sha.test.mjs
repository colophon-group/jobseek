import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const deployedSha = "a".repeat(40);
const currentMainSha = "b".repeat(40);

function runGuard({
  deployed = deployedSha,
  main = deployedSha,
  deploymentUrl = "https://jobseek-example.vercel.app",
} = {}) {
  const dir = mkdtempSync(join(tmpdir(), "vercel-production-sha-"));
  const gh = join(dir, "gh");
  const summary = join(dir, "summary.md");
  writeFileSync(
    gh,
    `#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "api" ]]; then
  printf '%s\\n' "$MOCK_MAIN_SHA"
  exit 0
fi
exit 64
`,
  );
  chmodSync(gh, 0o755);

  const result = spawnSync(
    "bash",
    [".github/scripts/verify-vercel-production-sha.sh"],
    {
      cwd: process.cwd(),
      env: {
        ...process.env,
        PATH: `${dir}:${process.env.PATH}`,
        GH_TOKEN: "test-token",
        LANG: "C",
        LC_ALL: "C",
        REPO: "colophon-group/jobseek",
        DEFAULT_BRANCH: "main",
        DEPLOYED_SHA: deployed,
        DEPLOYMENT_URL: deploymentUrl,
        GITHUB_STEP_SUMMARY: summary,
        MOCK_MAIN_SHA: main,
      },
      encoding: "utf8",
    },
  );

  let summaryText = "";
  try {
    summaryText = readFileSync(summary, "utf8");
  } catch {
    // Successful checks do not need a job summary.
  }
  rmSync(dir, { recursive: true, force: true });
  return { ...result, summaryText };
}

test("accepts a Vercel Production deployment at the current main SHA", () => {
  const result = runGuard();

  assert.equal(result.status, 0);
  assert.match(result.stdout, /vercel_production_sha_verified/);
  assert.equal(result.stderr, "");
  assert.equal(result.summaryText, "");
});

test("fails visibly when a stale main ancestor reaches production", () => {
  const result = runGuard({ main: currentMainSha });

  assert.equal(result.status, 1);
  assert.match(result.stdout, /::error title=Stale Vercel Production deployment/);
  assert.match(result.stdout, new RegExp(deployedSha));
  assert.match(result.stdout, new RegExp(currentMainSha));
  assert.match(result.summaryText, /vercel promote <deployment>/);
});

test("rejects malformed deployment input before calling GitHub", () => {
  const result = runGuard({ deployed: "not-a-sha" });

  assert.equal(result.status, 2);
  assert.match(result.stderr, /Invalid DEPLOYED_SHA/);
});
