#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { appendFileSync, readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import { isCrawlerDerivedBuildEligible } from "./check-crawler-version.mjs";

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

export function deriveCrawlerBuildVersion({
  sourceVersion,
  parentVersion,
  commitCount,
  sha,
  files,
}) {
  const source = parseVersion(sourceVersion, "Source VERSION");
  const parent = parentVersion
    ? parseVersion(parentVersion, "Parent VERSION")
    : null;

  if (parent && compareVersions(source, parent) < 0) {
    throw new Error(
      `apps/crawler/VERSION regressed: ${parentVersion.trim()} → ${sourceVersion.trim()}`,
    );
  }

  const cleanVersion = sourceVersion.trim();
  if (!parent || compareVersions(source, parent) > 0) {
    return {
      sourceVersion: cleanVersion,
      packageVersion: cleanVersion,
      imageTag: `v${cleanVersion}`,
      derived: false,
    };
  }

  if (!isCrawlerDerivedBuildEligible(files)) {
    throw new Error(
      "Unchanged crawler VERSION is only valid for a dependency-only or " +
        "deploy-infrastructure main commit",
    );
  }

  if (!/^\d+$/.test(String(commitCount)) || Number(commitCount) < 1) {
    throw new Error(`Commit count must be a positive integer, got ${commitCount}`);
  }
  const shortSha = sha.trim().slice(0, 12);
  if (!/^[0-9a-f]{7,12}$/i.test(shortSha)) {
    throw new Error(`Commit SHA is invalid: ${JSON.stringify(sha.trim())}`);
  }

  const suffix = `build.${commitCount}.g${shortSha.toLowerCase()}`;
  return {
    sourceVersion: cleanVersion,
    packageVersion: `${cleanVersion}+${suffix}`,
    imageTag: `v${cleanVersion}-${suffix}`,
    derived: true,
  };
}

function git(...args) {
  return execFileSync("git", args, { encoding: "utf8" }).trim();
}

function optionalArgument(name) {
  const index = process.argv.indexOf(name);
  return index === -1 ? null : process.argv[index + 1];
}

function main() {
  const versionPath = optionalArgument("--write-version") ?? "apps/crawler/VERSION";
  const githubOutput = optionalArgument("--github-output");
  const sourceVersion = readFileSync(versionPath, "utf8").trim();
  let parentVersion = null;
  try {
    parentVersion = git("show", `HEAD^:${versionPath}`);
  } catch {
    // The repository's first deployable commit has no parent to compare.
  }
  const result = deriveCrawlerBuildVersion({
    sourceVersion,
    parentVersion,
    commitCount: git("rev-list", "--first-parent", "--count", "HEAD"),
    sha: git("rev-parse", "HEAD"),
    files: git("diff", "--name-only", "HEAD^", "HEAD")
      .split("\n")
      .filter(Boolean),
  });

  writeFileSync(versionPath, `${result.packageVersion}\n`);
  const outputs = [
    `source_version=${result.sourceVersion}`,
    `package_version=${result.packageVersion}`,
    `image_tag=${result.imageTag}`,
    `derived=${result.derived}`,
  ].join("\n");
  if (githubOutput) appendFileSync(githubOutput, `${outputs}\n`);
  console.log(
    `Crawler build version: ${result.packageVersion} (${result.imageTag})`,
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main();
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  }
}
