import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  evaluateCrawlerVersion,
  isInactiveV1CandidateOnly,
  isInactiveV1PolicyInfrastructureCommit,
} from "./check-crawler-version.mjs";
import { deriveCrawlerBuildVersion } from "./derive-crawler-build-version.mjs";

const inactiveV1PolicyFiles = [
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

test("pure nonempty inactive runtime v1 candidate changes keep VERSION", () => {
  for (const files of [
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
    const result = evaluateCrawlerVersion({
      baseVersion: "0.13.525",
      prVersion: "0.13.525",
      author: "developer",
      files,
    });
    assert.equal(result.kind, "inactive-v1-candidate");
    assert.match(result.message, /#8046/);
  }
});

test("inactive runtime v1 candidate predicate is exact and nonempty", () => {
  assert.equal(isInactiveV1CandidateOnly([]), false);
  assert.equal(
    isInactiveV1CandidateOnly(["apps/crawler/contracts/v1/runtime.proto"]),
    true,
  );
  for (const path of [
    "apps/crawler/contracts/v1",
    "apps/crawler/contracts/v10/runtime.proto",
    "apps/crawler/contracts/v2/runtime.proto",
    "apps/crawler/src/contracts/v1/runtime.proto",
  ]) {
    assert.equal(isInactiveV1CandidateOnly([path]), false, path);
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
    git(repo, "commit", "--quiet", "-m", "candidate rename");
    const renamed = git(repo, "rev-parse", "HEAD");
    const samePrefix = runVersionCheck(repo, initial, renamed);
    assert.equal(samePrefix.status, 0, samePrefix.stderr);
    assert.match(samePrefix.stdout, /Inactive runtime v1 candidate/);

    rmSync(join(repo, "apps/crawler/contracts/v1/new.proto"));
    git(repo, "add", ".");
    git(repo, "commit", "--quiet", "-m", "candidate deletion");
    const deleted = git(repo, "rev-parse", "HEAD");
    const deletion = runVersionCheck(repo, renamed, deleted);
    assert.equal(deletion.status, 0, deletion.stderr);

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

test("candidate plus VERSION is rejected before a release can be accepted", () => {
  for (const prVersion of ["0.13.525", "0.13.526"] ) {
    assert.throws(
      () =>
        evaluateCrawlerVersion({
          baseVersion: "0.13.525",
          prVersion,
          author: "developer",
          files: [
            "apps/crawler/contracts/v1/runtime.proto",
            "apps/crawler/VERSION",
          ],
        }),
      /must not include apps\/crawler\/VERSION/,
    );
  }
});

test("mixed and cross-boundary candidate diffs retain normal version policy", () => {
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

test("the exact #8071 policy bridge self-hosts without VERSION", () => {
  assert.equal(
    isInactiveV1PolicyInfrastructureCommit(inactiveV1PolicyFiles),
    true,
  );
  assert.equal(
    evaluateCrawlerVersion({
      baseVersion: "0.13.525",
      prVersion: "0.13.525",
      author: "developer",
      files: inactiveV1PolicyFiles,
    }).kind,
    "inactive-v1-policy",
  );

  assert.equal(
    isInactiveV1PolicyInfrastructureCommit(inactiveV1PolicyFiles.slice(1)),
    false,
  );
  assert.throws(
    () =>
      evaluateCrawlerVersion({
        baseVersion: "0.13.525",
        prVersion: "0.13.525",
        author: "developer",
        files: [...inactiveV1PolicyFiles, "apps/crawler/src/cli.py"],
      }),
    /must be bumped/,
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

test("the exact #8071 policy bridge gets a deterministic derived build", () => {
  const result = deriveCrawlerBuildVersion({
    sourceVersion: "0.13.525",
    parentVersion: "0.13.525",
    commitCount: "7001",
    sha: "abc123def4567890",
    files: inactiveV1PolicyFiles,
  });
  assert.equal(result.packageVersion, "0.13.525+build.7001.gabc123def456");
  assert.equal(result.imageTag, "v0.13.525-build.7001.gabc123def456");
  assert.equal(result.derived, true);
});

test("derived builds reject candidate and #8071 policy mixtures", () => {
  for (const files of [
    ["apps/crawler/contracts/v1/runtime.proto"],
    [...inactiveV1PolicyFiles, "apps/crawler/contracts/v1/runtime.proto"],
    [...inactiveV1PolicyFiles, "apps/crawler/src/cli.py"],
    [...inactiveV1PolicyFiles, "apps/crawler/VERSION"],
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
