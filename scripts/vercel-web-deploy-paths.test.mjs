import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  chmodSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  classifyVercelWebChanges,
  isVercelWebInput,
} from "../.github/scripts/classify-vercel-web-change.mjs";
import {
  ScannerResponseError,
  verifyScannerResponse,
} from "../.github/scripts/verify-vercel-scanner-response.mjs";

const scannerVerifier = fileURLToPath(
  new URL("../.github/scripts/verify-vercel-scanner-response.mjs", import.meta.url),
);
const turboConfig = JSON.parse(readFileSync("turbo.json", "utf8"));

test("deploys web runtime and production-workflow inputs", () => {
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
    ".github/scripts/verify-vercel-scanner-response.mjs",
    ".github/scripts/verify-vercel-server-action-key.mjs",
    ".github/scripts/verify-vercel-promotion.mjs",
  ]) {
    assert.equal(isVercelWebInput(path), true, path);
  }
});

test("does not redeploy for unrelated crawler data, docs, or ops changes", () => {
  const result = classifyVercelWebChanges([
    "apps/crawler/data/company_descriptions.csv",
    "apps/crawler/src/core/monitors/workday.py",
    ".github/workflows/sync-data.yml",
    "docs/01-agent-workflow.md",
  ]);
  assert.deepEqual(result, { deploy: false, relevant: [] });
});

test("does not replace the web deployment for company registry changes", () => {
  assert.deepEqual(
    classifyVercelWebChanges(["apps/crawler/data/companies.csv"]),
    { deploy: false, relevant: [] },
  );
  assert.equal(isVercelWebInput("apps/crawler/data/companies.csv"), false);
  assert.deepEqual(
    classifyVercelWebChanges([
      "apps/crawler/data/companies.csv",
      "apps/web/app/page.tsx",
    ]),
    { deploy: true, relevant: ["apps/web/app/page.tsx"] },
  );
});

test("genuine web builds still regenerate from the current company registry", () => {
  const genericBuild = turboConfig.tasks.build;
  const webBuild = turboConfig.tasks["@jobseek/web#build"];
  assert.deepEqual(webBuild.inputs, [
    "$TURBO_DEFAULT$",
    "$TURBO_ROOT$/apps/crawler/data/companies.csv",
  ]);
  for (const key of ["dependsOn", "outputs", "env"]) {
    assert.deepEqual(webBuild[key], genericBuild[key], key);
  }
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
  assert.match(workflow, /REQUESTED_SHA: \$\{\{ inputs\.revision \}\}/);
  assert.match(workflow, /Requested revision \$REQUESTED_SHA is stale/);
  assert.match(workflow, /vercel@59\.3\.0 promote/);
  assert.match(workflow, /id: promote/);
  assert.match(workflow, /promoted=true/);
  assert.match(workflow, /REQUIRE_EXACT_PROMOTION/);
  assert.match(
    workflow,
    /if: steps\.promote\.outputs\.promoted == 'true'/,
  );
  assert.match(workflow, /verify-vercel-promotion\.mjs/);
  assert.match(
    workflow,
    /classify-vercel-web-change\.mjs[\s\S]{0,160}\$EXPECTED_SHA[\s\S]{0,80}\$current_main/,
  );
  assert.match(workflow, /Web change landed during Vercel promotion/);
  assert.match(
    workflow,
    /VERCEL_TOKEN: \$\{\{ secrets\.VERCEL_TOKEN \}\}/,
  );
  const curlLines = workflow
    .split("\n")
    .filter((line) => line.includes("vercel@59.3.0") && line.includes("curl"));
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
  assert.match(
    workflow,
    /scanner_headers=.*RUNNER_TEMP[\s\S]{0,500}--dump-header "\$scanner_headers"/,
  );
  assert.match(workflow, /umask 077/);
  assert.match(workflow, /chmod 600 "\$scanner_headers"/);
  assert.match(workflow, /trap 'rm -f -- "\$scanner_headers"' EXIT/);
  assert.match(workflow, /verify-vercel-scanner-response\.mjs/);
  assert.doesNotMatch(workflow, /\bcat\s+.*scanner_headers/);
  assert.match(workflow, /Smoke \$path -> HTTP \$status/);
  assert.match(workflow, /Scanner path exposed/);
  assert.match(workflow, /pnpm install --frozen-lockfile/);
  assert.equal(
    [...workflow.matchAll(/run: pnpm db:migrate:verify-head/g)].length,
    2,
  );
  assert.doesNotMatch(workflow, /db:migrate:apply-account-issuer/);
  assert.doesNotMatch(workflow, /run: pnpm db:migrate\s*$/m);
  assert.match(
    workflow,
    /DATABASE_URL_UNPOOLED: \$\{\{ secrets\.DATABASE_URL_UNPOOLED \}\}/,
  );
  const install = workflow.indexOf("Install locked dependencies");
  const initialHeadCheck = workflow.indexOf(
    "Require production database at checked-out migration head",
  );
  const pull = workflow.indexOf("Pull production project settings");
  const stagedSmoke = workflow.indexOf("Verify staged production functionality");
  const currentMainGuard = workflow.indexOf(
    "Require the staged revision is still main",
  );
  const finalHeadCheck = workflow.indexOf(
    "Reverify production migration head immediately before promotion",
  );
  const promotion = workflow.indexOf("Promote only if this SHA is still main");
  assert.ok(install < initialHeadCheck);
  assert.ok(initialHeadCheck < pull);
  assert.ok(stagedSmoke < currentMainGuard);
  assert.ok(currentMainGuard < finalHeadCheck);
  assert.ok(finalHeadCheck < promotion);
  assert.doesNotMatch(workflow, /--cwd=apps\/web/);
  assert.doesNotMatch(workflow, /--git-branch/);
  assert.match(workflow, /environment: Production/);
  assert.match(
    workflow,
    /vercel@59\.3\.0 pull[\s\S]{0,400}verify-vercel-server-action-key\.mjs[\s\S]{0,400}vercel@59\.3\.0 build/,
  );
  assert.doesNotMatch(workflow, /environment:\n\s+name: Production\n\s+url:/);
  assert.doesNotMatch(workflow, /pull_request:/);
});

test("accepts a normal final 404 scanner response", () => {
  assert.deepEqual(
    verifyScannerResponse(
      "404",
      "HTTP/2 404\r\ncontent-type: text/html\r\nset-cookie: private=value\r\n\r\n",
    ),
    { status: "404", outcome: "not_found" },
  );
});

test("accepts only an exact Vercel-mitigated final 403", () => {
  assert.deepEqual(
    verifyScannerResponse(
      "403",
      "HTTP/2 403\r\nx-vercel-mitigated: deny\r\ncache-control: private, no-store\r\n\r\n",
    ),
    { status: "403", outcome: "vercel_mitigated" },
  );
});

test("CLI reports only a safe scanner decision", () => {
  const directory = mkdtempSync(join(tmpdir(), "vercel-scanner-response-"));
  const headerPath = join(directory, "headers");
  const sensitiveValue = "sensitive-test-value";
  writeFileSync(
    headerPath,
    [
      "HTTP/2 403",
      "x-vercel-mitigated: deny",
      `set-cookie: session=${sensitiveValue}`,
      `authorization: Bearer ${sensitiveValue}`,
      "",
      "",
    ].join("\r\n"),
    { mode: 0o600 },
  );
  chmodSync(headerPath, 0o600);

  const result = spawnSync(process.execPath, [scannerVerifier, "403", headerPath], {
    encoding: "utf8",
  });
  rmSync(directory, { recursive: true, force: true });

  assert.equal(result.status, 0);
  assert.match(result.stdout, /HTTP 403 \(Vercel mitigation confirmed\)/);
  assert.equal(result.stderr, "");
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, new RegExp(sensitiveValue));
});

test("rejects a generic application 403", () => {
  assert.throws(
    () =>
      verifyScannerResponse(
        "403",
        "HTTP/2 403\r\ncontent-type: text/html\r\n\r\n",
      ),
    ScannerResponseError,
  );
});

test("rejects missing, malformed, or ambiguous mitigation evidence", () => {
  for (const headers of [
    "HTTP/2 403\r\nx-vercel-mitigated: block\r\n\r\n",
    "HTTP/2 403\r\nx-vercel-mitigated: Deny\r\n\r\n",
    "HTTP/2 403\r\nx-vercel-mitigated: deny, challenge\r\n\r\n",
    "HTTP/2 403\r\nx-vercel-mitigated: deny\r\nx-vercel-mitigated: deny\r\n\r\n",
    "HTTP/2 403\r\nx-vercel-mitigated deny\r\n\r\n",
    "HTTP/2 403\r\nx-vercel-mitigated: deny\r\n folded\r\n\r\n",
  ]) {
    assert.throws(
      () => verifyScannerResponse("403", headers),
      ScannerResponseError,
      headers,
    );
  }
});

test("rejects exposed 200 and redirect-to-200 responses", () => {
  assert.throws(
    () =>
      verifyScannerResponse(
        "200",
        "HTTP/2 200\r\ncontent-type: text/html\r\n\r\n",
      ),
    ScannerResponseError,
  );
  assert.throws(
    () =>
      verifyScannerResponse(
        "200",
        "HTTP/2 302\r\nlocation: /login\r\n\r\nHTTP/2 200\r\ncontent-type: text/html\r\n\r\n",
      ),
    ScannerResponseError,
  );
});

test("uses only the final block in a multi-response header file", () => {
  const earlierMitigation =
    "HTTP/2 403\r\nx-vercel-mitigated: deny\r\n\r\n";
  const finalGeneric403 = "HTTP/2 403\r\ncontent-type: text/html\r\n\r\n";

  assert.throws(
    () => verifyScannerResponse("403", earlierMitigation + finalGeneric403),
    ScannerResponseError,
  );
  assert.deepEqual(
    verifyScannerResponse(
      "403",
      "HTTP/2 302\r\nlocation: /blocked\r\n\r\n" + earlierMitigation,
    ),
    { status: "403", outcome: "vercel_mitigated" },
  );
});

test("rejects status/header disagreement and every other status", () => {
  assert.throws(
    () => verifyScannerResponse("403", "HTTP/2 404\r\n\r\n"),
    ScannerResponseError,
  );
  for (const status of ["301", "401", "429", "500"]) {
    assert.throws(
      () => verifyScannerResponse(status, `HTTP/2 ${status}\r\n\r\n`),
      ScannerResponseError,
    );
  }
});
