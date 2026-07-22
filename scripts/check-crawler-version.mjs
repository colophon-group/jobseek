#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const DEPENDABOT_LOGIN = "dependabot[bot]";
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

export function evaluateCrawlerVersion({
  baseVersion,
  prVersion,
  author,
  files,
}) {
  const base = parseVersion(baseVersion, "Base VERSION");
  const pr = parseVersion(prVersion, "PR VERSION");
  const comparison = compareVersions(pr, base);

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

  const uniqueFiles = [...new Set(files)].sort();
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
  const versionPath = "apps/crawler/VERSION";
  const baseVersion = git("show", `${baseSha}:${versionPath}`);
  const prVersion = git("show", `${headSha}:${versionPath}`);
  const files = git("diff", "--name-only", `${baseSha}...${headSha}`)
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
