import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { evaluateCrawlerVersion } from "./check-crawler-version.mjs";
import { deriveCrawlerBuildVersion } from "./derive-crawler-build-version.mjs";

const formerV1BridgeFiles = [
  ".github/scripts/check-crawler-deploy-gate.sh",
  ".github/workflows/deploy-ats-inventory.yml",
  ".github/workflows/deploy-crawler-browser.yml",
  "apps/crawler/tests/test_ats_inventory_deployment.py",
  "scripts/check-crawler-version.mjs",
  "scripts/ci-workflow.test.mjs",
  "scripts/crawler-runtime-contract.test.mjs",
  "scripts/crawler-version.test.mjs",
  "scripts/derive-crawler-runtime-contract.mjs",
];

function git(repo, ...args) {
  return execFileSync("git", ["-C", repo, ...args], {
    encoding: "utf8",
  }).trim();
}

function runVersionCheck(repo, base, head) {
  return spawnSync(
    "node",
    [
      join(process.cwd(), "scripts/check-crawler-version.mjs"),
      "--base",
      base,
      "--head",
      head,
      "--author",
      "developer",
    ],
    { cwd: repo, encoding: "utf8" },
  );
}

test("explicit crawler releases remain the default", () => {
  const result = evaluateCrawlerVersion({
    baseVersion: "0.13.152",
    prVersion: "0.13.153",
    author: "developer",
    files: ["apps/crawler/src/cli.py", "apps/crawler/VERSION"],
  });
  assert.equal(result.kind, "release");
});

test("runtime v1 changes require an ordinary crawler release", () => {
  for (const files of [
    ["apps/crawler/contracts/go.mod"],
    ["apps/crawler/contracts/go.sum"],
    ["apps/crawler/contracts/v1/runtime.proto"],
    [
      "apps/crawler/contracts/v1/runtime.proto",
      "apps/crawler/contracts/v1/fixtures/deleted.json",
    ],
    // A same-prefix rename is represented rename-safely by both paths.
    [
      "apps/crawler/contracts/v1/old-name.proto",
      "apps/crawler/contracts/v1/new-name.proto",
    ],
  ]) {
    assert.throws(
      () =>
        evaluateCrawlerVersion({
          baseVersion: "0.13.525",
          prVersion: "0.13.525",
          author: "developer",
          files,
        }),
      /must be bumped/,
    );
    assert.equal(
      evaluateCrawlerVersion({
        baseVersion: "0.13.525",
        prVersion: "0.13.526",
        author: "developer",
        files: [...files, "apps/crawler/VERSION"],
      }).kind,
      "release",
    );
  }
});

test("the executable version gate classifies old and new rename paths", () => {
  const repo = mkdtempSync(join(tmpdir(), "crawler-version-candidate-"));
  try {
    git(repo, "init", "--quiet");
    git(repo, "config", "user.email", "test@example.com");
    git(repo, "config", "user.name", "Test");
    mkdirSync(join(repo, "apps/crawler/contracts/v1"), { recursive: true });
    mkdirSync(join(repo, "apps/crawler/src"), { recursive: true });
    writeFileSync(join(repo, "apps/crawler/VERSION"), "0.13.525\n");
    writeFileSync(
      join(repo, "apps/crawler/contracts/v1/old.proto"),
      "syntax = \"proto3\";\n",
    );
    git(repo, "add", ".");
    git(repo, "commit", "--quiet", "-m", "base");
    const initial = git(repo, "rev-parse", "HEAD");

    git(
      repo,
      "mv",
      "apps/crawler/contracts/v1/old.proto",
      "apps/crawler/contracts/v1/new.proto",
    );
    git(repo, "commit", "--quiet", "-m", "runtime contract rename");
    const renamed = git(repo, "rev-parse", "HEAD");
    const samePrefix = runVersionCheck(repo, initial, renamed);
    assert.equal(samePrefix.status, 1, samePrefix.stderr);
    assert.match(samePrefix.stderr, /must be bumped/);

    rmSync(join(repo, "apps/crawler/contracts/v1/new.proto"));
    git(repo, "add", ".");
    git(repo, "commit", "--quiet", "-m", "runtime contract deletion");
    const deleted = git(repo, "rev-parse", "HEAD");
    const deletion = runVersionCheck(repo, renamed, deleted);
    assert.equal(deletion.status, 1, deletion.stderr);
    assert.match(deletion.stderr, /must be bumped/);

    writeFileSync(
      join(repo, "apps/crawler/contracts/v1/cross.py"),
      "VALUE = 1\n",
    );
    git(repo, "add", ".");
    git(repo, "commit", "--quiet", "-m", "candidate addition");
    const beforeCrossBoundary = git(repo, "rev-parse", "HEAD");
    git(
      repo,
      "mv",
      "apps/crawler/contracts/v1/cross.py",
      "apps/crawler/src/cross.py",
    );
    git(repo, "commit", "--quiet", "-m", "cross boundary rename");
    const crossBoundary = git(repo, "rev-parse", "HEAD");
    const crossing = runVersionCheck(repo, beforeCrossBoundary, crossBoundary);
    assert.equal(crossing.status, 1);
    assert.match(crossing.stderr, /must be bumped/);

    const empty = runVersionCheck(repo, crossBoundary, crossBoundary);
    assert.equal(empty.status, 1);
    assert.match(empty.stderr, /must be bumped/);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test("runtime v1 plus VERSION is accepted only as a monotonic release", () => {
  const files = [
    "apps/crawler/contracts/v1/runtime.proto",
    "apps/crawler/VERSION",
  ];
  assert.throws(
    () =>
      evaluateCrawlerVersion({
        baseVersion: "0.13.525",
        prVersion: "0.13.525",
        author: "developer",
        files,
      }),
    /must be bumped/,
  );
  assert.equal(
    evaluateCrawlerVersion({
      baseVersion: "0.13.525",
      prVersion: "0.13.526",
      author: "developer",
      files,
    }).kind,
    "release",
  );
});

test("mixed and cross-boundary contract diffs retain normal version policy", () => {
  for (const files of [
    [
      "apps/crawler/contracts/v1/runtime.proto",
      "apps/crawler/src/cli.py",
    ],
    [
      "apps/crawler/contracts/v1/runtime.proto",
      "apps/crawler/pyproject.toml",
    ],
    // Rename from active runtime into v1.
    [
      "apps/crawler/src/old_contract.py",
      "apps/crawler/contracts/v1/old_contract.py",
    ],
    // Rename from v1 into active runtime.
    [
      "apps/crawler/contracts/v1/new_contract.py",
      "apps/crawler/src/new_contract.py",
    ],
    ["apps/crawler/contracts/v2/runtime.proto"],
  ]) {
    assert.throws(
      () =>
        evaluateCrawlerVersion({
          baseVersion: "0.13.525",
          prVersion: "0.13.525",
          author: "developer",
          files,
        }),
      /must be bumped/,
    );
    assert.equal(
      evaluateCrawlerVersion({
        baseVersion: "0.13.525",
        prVersion: "0.13.526",
        author: "developer",
        files: [...files, "apps/crawler/VERSION"],
      }).kind,
      "release",
    );
  }
});

test("the former #8071 policy bridge requires an ordinary release", () => {
  assert.throws(
    () =>
      evaluateCrawlerVersion({
        baseVersion: "0.13.525",
        prVersion: "0.13.525",
        author: "developer",
        files: formerV1BridgeFiles,
      }),
    /must be bumped/,
  );
  assert.equal(
    evaluateCrawlerVersion({
      baseVersion: "0.13.525",
      prVersion: "0.13.526",
      author: "developer",
      files: [...formerV1BridgeFiles, "apps/crawler/VERSION"],
    }).kind,
    "release",
  );
});

test("dependency-only Dependabot updates may keep the base version", () => {
  const result = evaluateCrawlerVersion({
    baseVersion: "0.13.152",
    prVersion: "0.13.152",
    author: "dependabot[bot]",
    files: ["apps/crawler/pyproject.toml", "apps/crawler/uv.lock"],
  });
  assert.equal(result.kind, "dependabot-build");
});

test("transitive lockfile-only Dependabot updates are supported", () => {
  const result = evaluateCrawlerVersion({
    baseVersion: "0.13.152",
    prVersion: "0.13.152",
    author: "dependabot[bot]",
    files: ["apps/crawler/uv.lock"],
  });
  assert.equal(result.kind, "dependabot-build");
});

test("ws-package lockfile updates use the dependency-only policy", () => {
  const result = evaluateCrawlerVersion({
    baseVersion: "0.13.152",
    prVersion: "0.13.152",
    author: "dependabot[bot]",
    files: ["apps/crawler/ws-package/uv.lock"],
  });
  assert.equal(result.kind, "dependabot-build");
});

test("human-authored crawler changes still require a release bump", () => {
  assert.throws(
    () =>
      evaluateCrawlerVersion({
        baseVersion: "0.13.152",
        prVersion: "0.13.152",
        author: "developer",
        files: ["apps/crawler/uv.lock"],
      }),
    /must be bumped/,
  );
});

test("runtime taxonomy and contract-boundary changes require a release bump", () => {
  for (const file of [
    "apps/crawler/data/industries.csv",
    "apps/crawler/data/occupations.csv",
    "apps/crawler/data/seniority.csv",
    "apps/crawler/data/technologies.csv",
    "scripts/derive-crawler-runtime-contract.mjs",
    "scripts/verify-crawler-release-bridge.py",
  ]) {
    assert.throws(
      () =>
        evaluateCrawlerVersion({
          baseVersion: "0.13.152",
          prVersion: "0.13.152",
          author: "developer",
          files: [file],
        }),
      /must be bumped/,
      file,
    );
  }
});

test("Dependabot cannot bypass the gate for crawler source changes", () => {
  assert.throws(
    () =>
      evaluateCrawlerVersion({
        baseVersion: "0.13.152",
        prVersion: "0.13.152",
        author: "dependabot[bot]",
        files: ["apps/crawler/uv.lock", "apps/crawler/src/cli.py"],
      }),
    /non-dependency paths/,
  );
});

test("crawler version regressions always fail", () => {
  assert.throws(
    () =>
      evaluateCrawlerVersion({
        baseVersion: "0.13.152",
        prVersion: "0.13.151",
        author: "dependabot[bot]",
        files: ["apps/crawler/uv.lock"],
      }),
    /regressed/,
  );
});

test("explicit release builds retain their clean version and tag", () => {
  assert.deepEqual(
    deriveCrawlerBuildVersion({
      sourceVersion: "0.13.153",
      parentVersion: "0.13.152",
      commitCount: "6200",
      sha: "abcdef1234567890",
      files: ["apps/crawler/src/cli.py", "apps/crawler/VERSION"],
    }),
    {
      sourceVersion: "0.13.153",
      packageVersion: "0.13.153",
      imageTag: "v0.13.153",
      derived: false,
    },
  );
});

test("batched push releases may end in an unrelated commit", () => {
  assert.deepEqual(
    deriveCrawlerBuildVersion({
      sourceVersion: "0.13.153",
      parentVersion: "0.13.152",
      commitCount: "6203",
      sha: "fedcba9876543210",
      files: [
        "apps/crawler/src/cli.py",
        "apps/crawler/VERSION",
        ".github/scripts/label-pr.sh",
      ],
    }),
    {
      sourceVersion: "0.13.153",
      packageVersion: "0.13.153",
      imageTag: "v0.13.153",
      derived: false,
    },
  );
});

test("unchanged releases get deterministic commit-specific build versions", () => {
  assert.deepEqual(
    deriveCrawlerBuildVersion({
      sourceVersion: "0.13.152",
      parentVersion: "0.13.152",
      commitCount: "6201",
      sha: "ABCDEF1234567890",
      files: ["apps/crawler/pyproject.toml", "apps/crawler/uv.lock"],
    }),
    {
      sourceVersion: "0.13.152",
      packageVersion: "0.13.152+build.6201.gabcdef123456",
      imageTag: "v0.13.152-build.6201.gabcdef123456",
      derived: true,
    },
  );
});

test("deploy-infrastructure self-triggers get deterministic build versions", () => {
  assert.deepEqual(
    deriveCrawlerBuildVersion({
      sourceVersion: "0.13.152",
      parentVersion: "0.13.152",
      commitCount: "6202",
      sha: "123456789abcdef0",
      files: [
        ".github/workflows/deploy-crawler-browser.yml",
        "scripts/check-crawler-version.mjs",
        "scripts/crawler-version.test.mjs",
        "scripts/derive-crawler-build-version.mjs",
      ],
    }),
    {
      sourceVersion: "0.13.152",
      packageVersion: "0.13.152+build.6202.g123456789abc",
      imageTag: "v0.13.152-build.6202.g123456789abc",
      derived: true,
    },
  );
});

test("derived builds reject active contracts and the former #8071 bridge", () => {
  for (const files of [
    ["apps/crawler/contracts/v1/runtime.proto"],
    formerV1BridgeFiles,
    [...formerV1BridgeFiles, "apps/crawler/contracts/v1/runtime.proto"],
    [...formerV1BridgeFiles, "apps/crawler/src/cli.py"],
    [...formerV1BridgeFiles, "apps/crawler/VERSION"],
  ]) {
    assert.throws(
      () =>
        deriveCrawlerBuildVersion({
          sourceVersion: "0.13.525",
          parentVersion: "0.13.525",
          commitCount: "7001",
          sha: "abc123def4567890",
          files,
        }),
      /dependency-only or deploy-infrastructure main commit/,
    );
  }
});

test("deploy-infrastructure self-triggers cannot hide crawler source changes", () => {
  assert.throws(
    () =>
      deriveCrawlerBuildVersion({
        sourceVersion: "0.13.152",
        parentVersion: "0.13.152",
        commitCount: "6202",
        sha: "123456789abcdef0",
        files: [
          ".github/workflows/deploy-crawler-browser.yml",
          "scripts/derive-crawler-build-version.mjs",
          "apps/crawler/src/cli.py",
        ],
      }),
    /dependency-only or deploy-infrastructure main commit/,
  );
});

test("deployment does not derive versions for arbitrary unchanged code", () => {
  assert.throws(
    () =>
      deriveCrawlerBuildVersion({
        sourceVersion: "0.13.152",
        parentVersion: "0.13.152",
        commitCount: "6201",
        sha: "abcdef1234567890",
        files: ["apps/crawler/src/cli.py"],
      }),
    /dependency-only or deploy-infrastructure main commit/,
  );
});

test("deployment refuses a source-version rollback", () => {
  assert.throws(
    () =>
      deriveCrawlerBuildVersion({
        sourceVersion: "0.13.151",
        parentVersion: "0.13.152",
        commitCount: "6201",
        sha: "abcdef1234567890",
        files: ["apps/crawler/uv.lock"],
      }),
    /regressed/,
  );
});
