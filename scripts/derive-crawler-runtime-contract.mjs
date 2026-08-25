#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const EXTRA_RUNTIME_PATHS = new Set([
  ".github/workflows/deploy-crawler-browser.yml",
  "scripts/derive-crawler-runtime-contract.mjs",
]);
const RUNTIME_DATA_PATHS = new Set([
  "apps/crawler/data/industries.csv",
  "apps/crawler/data/occupations.csv",
  "apps/crawler/data/seniority.csv",
  "apps/crawler/data/technologies.csv",
]);

export function isCrawlerRuntimePath(path) {
  if (EXTRA_RUNTIME_PATHS.has(path)) return true;
  if (RUNTIME_DATA_PATHS.has(path)) return true;
  if (!path.startsWith("apps/crawler/")) return false;

  const relative = path.slice("apps/crawler/".length);
  return (
    !relative.startsWith("data/") &&
    !relative.startsWith("traces/") &&
    !relative.startsWith("ws-package/") &&
    !relative.endsWith(".md")
  );
}

export function isPublishableCrawlerDataPath(path) {
  return path.startsWith("apps/crawler/data/") && path.endsWith(".csv");
}

function deriveContract(entries, predicate, label) {
  const runtimeEntries = entries
    .filter(({ path }) => predicate(path))
    .sort((left, right) =>
      left.path < right.path ? -1 : left.path > right.path ? 1 : 0,
    );
  if (runtimeEntries.length === 0) {
    throw new Error(`${label} contract contains no files`);
  }

  const hash = createHash("sha256");
  for (const { mode, type, oid, path } of runtimeEntries) {
    if (!/^[0-7]{6}$/.test(mode)) throw new Error(`Invalid git mode for ${path}`);
    if (!/^[0-9a-f]{40,64}$/.test(oid)) throw new Error(`Invalid git object ID for ${path}`);
    hash.update(`${mode} ${type} ${oid}\t${path}\0`);
  }
  return hash.digest("hex");
}

export function deriveCrawlerRuntimeContract(entries) {
  return deriveContract(entries, isCrawlerRuntimePath, "Crawler runtime");
}

export function deriveCrawlerDataContract(entries) {
  return deriveContract(entries, isPublishableCrawlerDataPath, "Crawler data");
}

function requiredArgument(name) {
  const index = process.argv.indexOf(name);
  if (index === -1 || !process.argv[index + 1]) {
    throw new Error(`Missing required argument ${name}`);
  }
  return process.argv[index + 1];
}

function optionalArgument(name) {
  const index = process.argv.indexOf(name);
  return index === -1 ? null : process.argv[index + 1];
}

function readGitEntries(repo, revision) {
  if (!/^[0-9a-f]{40}$/.test(revision)) {
    throw new Error("Revision must be a full lowercase Git commit SHA");
  }
  const output = execFileSync(
    "git",
    [
      "-C",
      repo,
      "ls-tree",
      "-rz",
      "--full-tree",
      revision,
      "--",
      "apps/crawler",
      ...EXTRA_RUNTIME_PATHS,
    ],
    { encoding: "buffer" },
  );
  return output
    .toString("utf8")
    .split("\0")
    .filter(Boolean)
    .map((entry) => {
      const match = /^([0-7]{6}) ([^ ]+) ([0-9a-f]{40,64})\t([\s\S]+)$/.exec(
        entry,
      );
      if (!match) {
        throw new Error(`Unable to parse git tree entry ${JSON.stringify(entry)}`);
      }
      const [, mode, type, oid, path] = match;
      return { mode, type, oid, path };
    });
}

function main() {
  const revision = requiredArgument("--revision");
  const repo = optionalArgument("--repo") ?? ".";
  const kind = optionalArgument("--kind") ?? "runtime";
  const githubOutput = optionalArgument("--github-output");
  if (!new Set(["data", "runtime"]).has(kind)) {
    throw new Error("Contract kind must be data or runtime");
  }
  const entries = readGitEntries(repo, revision);
  const contract =
    kind === "data"
      ? deriveCrawlerDataContract(entries)
      : deriveCrawlerRuntimeContract(entries);
  if (githubOutput) {
    appendFileSync(
      githubOutput,
      `${kind}_contract_sha256=${contract}\n`,
    );
  }
  console.log(contract);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main();
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  }
}
