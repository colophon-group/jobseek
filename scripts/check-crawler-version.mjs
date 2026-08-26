#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const DEPENDABOT_LOGIN = "dependabot[bot]";
const CRAWLER_DEPLOY_WORKFLOW =
  ".github/workflows/deploy-crawler-browser.yml";
const CRAWLER_VERSION_PATH = "apps/crawler/VERSION";
const INACTIVE_V1_CANDIDATE_PREFIX = "apps/crawler/contracts/v1/";
// Temporary, exact release-policy bridge for #8071. #8046 must remove this
// exception when runtime v1 is packaged and activated.
const INACTIVE_V1_POLICY_FILES = new Set([
  ".github/scripts/check-crawler-deploy-gate.sh",
  ".github/workflows/deploy-ats-inventory.yml",
  ".github/workflows/deploy-crawler-browser.yml",
  "apps/crawler/tests/test_ats_inventory_deployment.py",
  "scripts/check-crawler-version.mjs",
  "scripts/ci-workflow.test.mjs",
  "scripts/crawler-runtime-contract.test.mjs",
  "scripts/crawler-version.test.mjs",
  "scripts/derive-crawler-runtime-contract.mjs",
]);
const DEPENDENCY_FILES = new Set([
  "apps/crawler/Dockerfile",
  "apps/crawler/docker-compose.yml",
  "apps/crawler/pyproject.toml",
  "apps/crawler/uv.lock",
  "apps/crawler/ws-package/pyproject.toml",
  "apps/crawler/ws-package/uv.lock",
]);

function parseVersion(value, label) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(value.trim());
  if (!match) {
    throw new Error(`${label} must be a major.minor.patch version, got ${JSON.stringify(value.trim())}`);
  }
  return match.slice(1).map(Number);
}

function compareVersions(left, right) {
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return left[index] - right[index];
  }
  return 0;
}

export function isCrawlerDependencyOnly(files) {
  const uniqueFiles = [...new Set(files)].sort();
  return (
    uniqueFiles.length > 0 &&
    uniqueFiles.every((file) => DEPENDENCY_FILES.has(file))
  );
}

export function isInactiveV1CandidateOnly(files) {
  const uniqueFiles = [...new Set(files)].sort();
  return (
    uniqueFiles.length > 0 &&
    uniqueFiles.every((file) => file.startsWith(INACTIVE_V1_CANDIDATE_PREFIX))
  );
}

export function isInactiveV1PolicyInfrastructureCommit(files) {
  const uniqueFiles = [...new Set(files)].sort();
  return (
    uniqueFiles.length === INACTIVE_V1_POLICY_FILES.size &&
    uniqueFiles.every((file) => INACTIVE_V1_POLICY_FILES.has(file))
  );
}

function isInactiveV1CandidatePlusVersionOnly(files) {
  const uniqueFiles = [...new Set(files)].sort();
  return (
    uniqueFiles.includes(CRAWLER_VERSION_PATH) &&
    uniqueFiles.some((file) => file.startsWith(INACTIVE_V1_CANDIDATE_PREFIX)) &&
    uniqueFiles.every(
      (file) =>
        file === CRAWLER_VERSION_PATH ||
        file.startsWith(INACTIVE_V1_CANDIDATE_PREFIX),
    )
  );
}

export function isCrawlerDeployInfrastructureCommit(files) {
  const uniqueFiles = [...new Set(files)].sort();
  return (
    uniqueFiles.includes(CRAWLER_DEPLOY_WORKFLOW) &&
    uniqueFiles.every(
      (file) =>
        !file.startsWith("apps/crawler/") || DEPENDENCY_FILES.has(file),
    )
  );
}

export function isCrawlerDerivedBuildEligible(files) {
  return (
    isCrawlerDependencyOnly(files) ||
    isCrawlerDeployInfrastructureCommit(files) ||
    isInactiveV1PolicyInfrastructureCommit(files)
  );
}

export function evaluateCrawlerVersion({
  baseVersion,
  prVersion,
  author,
  files,
}) {
  const uniqueFiles = [...new Set(files)].sort();

  // Check the attempted candidate exemption before accepting any VERSION
  // increase. A candidate plus VERSION diff is mixed by definition and must
  // not turn an inactive artifact edit into a release.
  if (isInactiveV1CandidatePlusVersionOnly(uniqueFiles)) {
    throw new Error(
      "inactive runtime v1 candidate changes must not include apps/crawler/VERSION",
    );
  }

  const base = parseVersion(baseVersion, "Base VERSION");
  const pr = parseVersion(prVersion, "PR VERSION");
  const comparison = compareVersions(pr, base);

  if (isInactiveV1CandidateOnly(uniqueFiles)) {
    if (comparison !== 0) {
      throw new Error(
        "inactive runtime v1 candidate-only changes must keep apps/crawler/VERSION unchanged",
      );
    }
    return {
      kind: "inactive-v1-candidate",
      message:
        `Inactive runtime v1 candidate keeps ${prVersion.trim()}; ` +
        "#8046 must revoke this temporary #8071 exemption on activation",
    };
  }

  if (isInactiveV1PolicyInfrastructureCommit(uniqueFiles)) {
    if (comparison !== 0) {
      throw new Error(
        "the exact #8071 release-policy bridge must keep apps/crawler/VERSION unchanged",
      );
    }
    return {
      kind: "inactive-v1-policy",
      message:
        `Exact #8071 inactive-v1 policy bridge keeps ${prVersion.trim()}; ` +
        "#8046 owns mandatory revocation",
    };
  }

  if (comparison > 0) {
    return {
      kind: "release",
      message: `VERSION bumped: ${baseVersion.trim()} → ${prVersion.trim()}`,
    };
  }
  if (comparison < 0) {
    throw new Error(
      `apps/crawler/VERSION regressed: ${baseVersion.trim()} → ${prVersion.trim()}`,
    );
  }

  const dependencyOnly = isCrawlerDependencyOnly(uniqueFiles);

  if (author === DEPENDABOT_LOGIN && dependencyOnly) {
    return {
      kind: "dependabot-build",
      message:
        `Dependabot dependency-only update keeps ${prVersion.trim()}; ` +
        "deployment will derive a commit-specific build version",
    };
  }

  const detail =
    author === DEPENDABOT_LOGIN
      ? `Dependabot diff includes non-dependency paths: ${uniqueFiles.join(", ")}`
      : `PR author is ${author || "unknown"}, not ${DEPENDABOT_LOGIN}`;
  throw new Error(
    `apps/crawler/VERSION must be bumped for crawler changes ` +
      `(base: ${baseVersion.trim()}, PR: ${prVersion.trim()}). ${detail}`,
  );
}

function git(...args) {
  return execFileSync("git", args, { encoding: "utf8" }).trim();
}

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index === -1 || !process.argv[index + 1]) {
    throw new Error(`Missing required argument ${name}`);
  }
  return process.argv[index + 1];
}

function main() {
  const baseSha = argument("--base");
  const headSha = argument("--head");
  const author = argument("--author");
  const versionPath = CRAWLER_VERSION_PATH;
  const baseVersion = git("show", `${baseSha}:${versionPath}`);
  const prVersion = git("show", `${headSha}:${versionPath}`);
  // Disable rename detection so both old and new paths participate in the
  // candidate-only predicate.
  const files = git(
    "diff",
    "--name-only",
    "--no-renames",
    `${baseSha}...${headSha}`,
  )
    .split("\n")
    .filter(Boolean);

  const result = evaluateCrawlerVersion({
    baseVersion,
    prVersion,
    author,
    files,
  });
  console.log(result.message);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main();
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  }
}
