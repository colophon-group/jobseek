import assert from "node:assert/strict";
import test from "node:test";

import { evaluateCrawlerVersion } from "./check-crawler-version.mjs";
import { deriveCrawlerBuildVersion } from "./derive-crawler-build-version.mjs";

test("explicit crawler releases remain the default", () => {
  const result = evaluateCrawlerVersion({
    baseVersion: "0.13.152",
    prVersion: "0.13.153",
    author: "developer",
    files: ["apps/crawler/src/cli.py", "apps/crawler/VERSION"],
  });
  assert.equal(result.kind, "release");
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
