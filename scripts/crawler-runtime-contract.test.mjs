import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  deriveCrawlerDataContract,
  deriveCrawlerRuntimeContract,
  deriveCrawlerRuntimeAttestation,
  isCrawlerRuntimePath,
  isPublishableCrawlerDataPath,
  readGitEntries,
} from "./derive-crawler-runtime-contract.mjs";

const entry = (path, oid) => ({
  mode: "100644",
  type: "blob",
  oid,
  path,
  contentSha256: createHash("sha256").update(oid).digest("hex"),
});

test("runtime contract matches the crawler deploy boundary", () => {
  for (const path of [
    "apps/crawler/src/core/monitors/dom.py",
    "apps/crawler/deploy.sh",
    "apps/crawler/docker-compose.yml",
    "apps/crawler/VERSION",
    "apps/crawler/data/industries.csv",
    "apps/crawler/data/occupations.csv",
    "apps/crawler/data/seniority.csv",
    "apps/crawler/data/technologies.csv",
    ".github/workflows/deploy-crawler-browser.yml",
    "scripts/derive-crawler-runtime-contract.mjs",
    "scripts/verify-crawler-release-bridge.py",
  ]) {
    assert.equal(isCrawlerRuntimePath(path), true, path);
  }

  for (const path of [
    "apps/crawler/contracts/v1/runtime.proto",
    "apps/crawler/contracts/v1/fixtures/replay.json",
    "apps/crawler/data/images/example/logo.png",
    "apps/crawler/traces/example.json",
    "apps/crawler/ws-package/pyproject.toml",
    "apps/crawler/README.md",
    "apps/crawler/docs/operator.md",
    ".github/workflows/sync-data.yml",
  ]) {
    assert.equal(isCrawlerRuntimePath(path), false, path);
  }

  for (const path of [
    "apps/crawler/contracts/v2/runtime.proto",
    "apps/crawler/contracts/v10/runtime.proto",
    "apps/crawler/contracts/v1",
  ]) {
    assert.equal(isCrawlerRuntimePath(path), true, path);
  }
});

test("publishable data contract covers every crawler CSV but no image assets", () => {
  for (const path of [
    "apps/crawler/data/boards.csv",
    "apps/crawler/data/nested/example.csv",
    "apps/crawler/data/industries.csv",
    "apps/crawler/data/occupations.csv",
    "apps/crawler/data/seniority.csv",
    "apps/crawler/data/technologies.csv",
  ]) {
    assert.equal(isPublishableCrawlerDataPath(path), true, path);
  }
  for (const path of [
    "apps/crawler/data/images/example/logo.png",
    "apps/crawler/src/cli.py",
  ]) {
    assert.equal(isPublishableCrawlerDataPath(path), false, path);
  }
});

test("data-only changes preserve the runtime contract", () => {
  const runtime = entry("apps/crawler/src/cli.py", "a".repeat(40));
  const workflow = entry(
    ".github/workflows/deploy-crawler-browser.yml",
    "b".repeat(40),
  );
  const before = [
    runtime,
    workflow,
    entry("apps/crawler/data/boards.csv", "c".repeat(40)),
  ];
  const after = [
    runtime,
    workflow,
    entry("apps/crawler/data/boards.csv", "d".repeat(40)),
  ];

  assert.equal(
    deriveCrawlerRuntimeContract(before),
    deriveCrawlerRuntimeContract(after),
  );
});

test("inactive runtime v1 candidates do not alter the active runtime digest", () => {
  const source = entry("apps/crawler/src/cli.py", "a".repeat(40));
  const workflow = entry(
    ".github/workflows/deploy-crawler-browser.yml",
    "b".repeat(40),
  );
  const baseline = deriveCrawlerRuntimeContract([source, workflow]);

  assert.equal(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/contracts/v1/runtime.proto", "c".repeat(40)),
    ]),
  );
  assert.equal(
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/contracts/v1/runtime.proto", "c".repeat(40)),
    ]),
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/contracts/v1/runtime.proto", "d".repeat(40)),
      entry("apps/crawler/contracts/v1/new.proto", "e".repeat(40)),
    ]),
  );
});

test("mixed, other-version, and cross-boundary changes alter the runtime digest", () => {
  const source = entry("apps/crawler/src/contract.py", "a".repeat(40));
  const workflow = entry(
    ".github/workflows/deploy-crawler-browser.yml",
    "b".repeat(40),
  );
  const candidate = entry(
    "apps/crawler/contracts/v1/runtime.proto",
    "c".repeat(40),
  );
  const baseline = deriveCrawlerRuntimeContract([source, workflow, candidate]);

  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      workflow,
      entry("apps/crawler/contracts/v1/contract.py", "a".repeat(40)),
      candidate,
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      candidate,
      entry("apps/crawler/src/new.py", "d".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      candidate,
      entry("apps/crawler/contracts/v2/runtime.proto", "e".repeat(40)),
    ]),
  );
});

test("runtime and rollout changes alter the contract", () => {
  const source = entry("apps/crawler/src/cli.py", "a".repeat(40));
  const workflow = entry(
    ".github/workflows/deploy-crawler-browser.yml",
    "b".repeat(40),
  );
  const baseline = deriveCrawlerRuntimeContract([source, workflow]);

  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      entry("apps/crawler/src/cli.py", "c".repeat(40)),
      workflow,
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      entry(".github/workflows/deploy-crawler-browser.yml", "d".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/data/industries.csv", "e".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/data/occupations.csv", "e".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/data/seniority.csv", "f".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerRuntimeContract([
      source,
      workflow,
      entry("apps/crawler/data/technologies.csv", "f".repeat(40)),
    ]),
  );
});

test("data contract changes for CSV content but ignores runtime-only advances", () => {
  const boards = entry("apps/crawler/data/boards.csv", "a".repeat(40));
  const companies = entry("apps/crawler/data/companies.csv", "b".repeat(40));
  const baseline = deriveCrawlerDataContract([
    boards,
    companies,
    entry("apps/crawler/src/cli.py", "c".repeat(40)),
  ]);

  assert.equal(
    baseline,
    deriveCrawlerDataContract([
      boards,
      companies,
      entry("apps/crawler/src/cli.py", "d".repeat(40)),
    ]),
  );
  assert.notEqual(
    baseline,
    deriveCrawlerDataContract([
      entry("apps/crawler/data/boards.csv", "e".repeat(40)),
      companies,
    ]),
  );
});

test("data contract equals the host-recomputable canonical manifest digest", () => {
  const boards = entry("apps/crawler/data/boards.csv", "a".repeat(40));
  const companies = entry("apps/crawler/data/companies.csv", "b".repeat(40));
  const manifest =
    `${boards.contentSha256}  boards.csv\n` +
    `${companies.contentSha256}  companies.csv\n`;
  assert.equal(
    deriveCrawlerDataContract([companies, boards]),
    createHash("sha256").update(manifest).digest("hex"),
  );
});

test("git data entries hash CSV blobs larger than Node's default buffer", () => {
  const repo = mkdtempSync(join(tmpdir(), "crawler-contract-"));
  try {
    execFileSync("git", ["-C", repo, "init", "--quiet"]);
    execFileSync("git", ["-C", repo, "config", "user.email", "test@example.com"]);
    execFileSync("git", ["-C", repo, "config", "user.name", "Test"]);
    mkdirSync(join(repo, "apps/crawler/data"), { recursive: true });
    const content = Buffer.alloc(2 * 1024 * 1024, "a");
    writeFileSync(join(repo, "apps/crawler/data/companies.csv"), content);
    execFileSync("git", ["-C", repo, "add", "."]);
    execFileSync("git", ["-C", repo, "commit", "--quiet", "-m", "fixture"]);
    const revision = execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }).trim();

    const entries = readGitEntries(repo, revision, true);
    assert.equal(entries.length, 1);
    assert.equal(
      entries[0].contentSha256,
      createHash("sha256").update(content).digest("hex"),
    );
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test("runtime attestation lists only the immutable first-parent runtime epoch", () => {
  const repo = mkdtempSync(join(tmpdir(), "crawler-runtime-attestation-"));
  try {
    execFileSync("git", ["-C", repo, "init", "--quiet"]);
    execFileSync("git", ["-C", repo, "config", "user.email", "test@example.com"]);
    execFileSync("git", ["-C", repo, "config", "user.name", "Test"]);
    mkdirSync(join(repo, "apps/crawler/src"), { recursive: true });
    mkdirSync(join(repo, "apps/crawler/data"), { recursive: true });
    writeFileSync(join(repo, "apps/crawler/src/cli.py"), "print('old')\n");
    writeFileSync(join(repo, "apps/crawler/data/boards.csv"), "slug\na\n");
    execFileSync("git", ["-C", repo, "add", "."]);
    execFileSync("git", ["-C", repo, "commit", "--quiet", "-m", "runtime"]);
    const initial = execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }).trim();

    writeFileSync(join(repo, "apps/crawler/data/boards.csv"), "slug\nb\n");
    execFileSync("git", ["-C", repo, "add", "."]);
    execFileSync("git", ["-C", repo, "commit", "--quiet", "-m", "data"]);
    const dataOnly = execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }).trim();
    const attestation = deriveCrawlerRuntimeAttestation(repo, dataOnly);
    assert.deepEqual(attestation.compatibleRevisions, [dataOnly, initial]);
    assert.match(
      attestation.text,
      new RegExp(
        `^RUNTIME_ATTESTATION_FORMAT_VERSION=1\\n` +
          `PREVIOUS_REVISION=${dataOnly}\\n` +
          `RUNTIME_CONTRACT_SHA256=${attestation.contract}\\n` +
          `COMPATIBLE_REVISION=${dataOnly}\\n` +
          `COMPATIBLE_REVISION=${initial}\\n$`,
      ),
    );

    writeFileSync(join(repo, "apps/crawler/src/cli.py"), "print('new')\n");
    execFileSync("git", ["-C", repo, "add", "."]);
    execFileSync("git", ["-C", repo, "commit", "--quiet", "-m", "new runtime"]);
    const changed = execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }).trim();
    assert.deepEqual(
      deriveCrawlerRuntimeAttestation(repo, changed).compatibleRevisions,
      [changed],
    );
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test("inactive runtime v1 commits remain in the rollback-compatible epoch", () => {
  const repo = mkdtempSync(join(tmpdir(), "crawler-candidate-attestation-"));
  try {
    execFileSync("git", ["-C", repo, "init", "--quiet"]);
    execFileSync("git", ["-C", repo, "config", "user.email", "test@example.com"]);
    execFileSync("git", ["-C", repo, "config", "user.name", "Test"]);
    mkdirSync(join(repo, "apps/crawler/src"), { recursive: true });
    mkdirSync(join(repo, "apps/crawler/contracts/v1"), { recursive: true });
    writeFileSync(join(repo, "apps/crawler/src/cli.py"), "print('runtime')\n");
    execFileSync("git", ["-C", repo, "add", "."]);
    execFileSync("git", ["-C", repo, "commit", "--quiet", "-m", "runtime"]);
    const initial = execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }).trim();

    writeFileSync(
      join(repo, "apps/crawler/contracts/v1/runtime.proto"),
      "syntax = \"proto3\";\n",
    );
    execFileSync("git", ["-C", repo, "add", "."]);
    execFileSync("git", ["-C", repo, "commit", "--quiet", "-m", "candidate"]);
    const candidate = execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }).trim();
    assert.deepEqual(
      deriveCrawlerRuntimeAttestation(repo, candidate).compatibleRevisions,
      [candidate, initial],
    );

    writeFileSync(join(repo, "apps/crawler/src/cli.py"), "print('changed')\n");
    execFileSync("git", ["-C", repo, "add", "."]);
    execFileSync("git", ["-C", repo, "commit", "--quiet", "-m", "mixed"]);
    const changed = execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], {
      encoding: "utf8",
    }).trim();
    assert.deepEqual(
      deriveCrawlerRuntimeAttestation(repo, changed).compatibleRevisions,
      [changed],
    );
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});
