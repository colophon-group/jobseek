#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const EXTRA_RUNTIME_PATHS = new Set([
  ".github/workflows/deploy-crawler-browser.yml",
  "scripts/derive-crawler-runtime-contract.mjs",
  "scripts/verify-crawler-release-bridge.py",
]);
const RUNTIME_DATA_PATHS = new Set([
  "apps/crawler/data/industries.csv",
  "apps/crawler/data/occupations.csv",
  "apps/crawler/data/seniority.csv",
  "apps/crawler/data/technologies.csv",
]);
const MAX_CRAWLER_DATA_BLOB_BYTES = 64 * 1024 * 1024;

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
  const dataEntries = entries
    .filter(({ path }) => isPublishableCrawlerDataPath(path))
    .sort((left, right) =>
      left.path < right.path ? -1 : left.path > right.path ? 1 : 0,
    );
  if (dataEntries.length === 0) {
    throw new Error("Crawler data contract contains no files");
  }
  const manifest = dataEntries
    .map(({ contentSha256, path }) => {
      const relative = path.slice("apps/crawler/data/".length);
      if (
        !/^[A-Za-z0-9._/-]+\.csv$/.test(relative) ||
        relative.split("/").includes("..")
      ) {
        throw new Error(`Invalid publishable crawler data path ${path}`);
      }
      if (!/^[0-9a-f]{64}$/.test(contentSha256 ?? "")) {
        throw new Error(`Missing canonical content digest for ${path}`);
      }
      return `${contentSha256}  ${relative}\n`;
    })
    .join("");
  return createHash("sha256").update(manifest).digest("hex");
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

export function readGitEntries(repo, revision, includeDataContents) {
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
  const entries = output
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
  if (!includeDataContents) return entries;
  return entries.map((entry) => {
    if (!isPublishableCrawlerDataPath(entry.path)) return entry;
    if (entry.type !== "blob") {
      throw new Error(`Publishable crawler data is not a blob: ${entry.path}`);
    }
    const content = execFileSync(
      "git",
      ["-C", repo, "cat-file", "blob", entry.oid],
      {
        encoding: "buffer",
        maxBuffer: MAX_CRAWLER_DATA_BLOB_BYTES,
      },
    );
    return {
      ...entry,
      contentSha256: createHash("sha256").update(content).digest("hex"),
    };
  });
}

export function deriveCrawlerRuntimeAttestation(repo, previousRevision) {
  if (!/^[0-9a-f]{40}$/.test(previousRevision)) {
    throw new Error("Previous revision must be a full lowercase Git commit SHA");
  }
  const expectedContract = deriveCrawlerRuntimeContract(
    readGitEntries(repo, previousRevision, false),
  );
  const revisions = execFileSync(
    "git",
    ["-C", repo, "rev-list", "--first-parent", previousRevision],
    { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 },
  )
    .trim()
    .split("\n")
    .filter(Boolean);
  const compatibleRevisions = [];
  for (const revision of revisions) {
    if (!/^[0-9a-f]{40}$/.test(revision)) {
      throw new Error(`Invalid first-parent revision ${revision}`);
    }
    const contract = deriveCrawlerRuntimeContract(
      readGitEntries(repo, revision, false),
    );
    if (contract !== expectedContract) break;
    compatibleRevisions.push(revision);
  }
  if (compatibleRevisions[0] !== previousRevision) {
    throw new Error("Runtime attestation does not start at the previous revision");
  }
  return {
    contract: expectedContract,
    compatibleRevisions,
    text:
      "RUNTIME_ATTESTATION_FORMAT_VERSION=1\n" +
      `PREVIOUS_REVISION=${previousRevision}\n` +
      `RUNTIME_CONTRACT_SHA256=${expectedContract}\n` +
      compatibleRevisions
        .map((revision) => `COMPATIBLE_REVISION=${revision}\n`)
        .join(""),
  };
}

function main() {
  const revision = requiredArgument("--revision");
  const repo = optionalArgument("--repo") ?? ".";
  const kind = optionalArgument("--kind") ?? "runtime";
  const githubOutput = optionalArgument("--github-output");
  const runtimeAttestationOut = optionalArgument("--runtime-attestation-out");
  if (!new Set(["data", "runtime"]).has(kind)) {
    throw new Error("Contract kind must be data or runtime");
  }
  const entries = readGitEntries(repo, revision, kind === "data");
  const contract =
    kind === "data"
      ? deriveCrawlerDataContract(entries)
      : deriveCrawlerRuntimeContract(entries);
  if (runtimeAttestationOut) {
    if (kind !== "runtime") {
      throw new Error("Runtime attestation output is only valid for a runtime contract");
    }
    const attestation = deriveCrawlerRuntimeAttestation(repo, revision);
    if (attestation.contract !== contract) {
      throw new Error("Runtime attestation disagrees with the derived contract");
    }
    writeFileSync(runtimeAttestationOut, attestation.text, {
      encoding: "utf8",
      mode: 0o644,
    });
  }
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
