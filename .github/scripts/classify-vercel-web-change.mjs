#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const EXACT_WEB_INPUTS = new Set([
  ".github/scripts/classify-vercel-web-change.mjs",
  ".github/workflows/deploy-web-production.yml",
  "package.json",
  "pnpm-lock.yaml",
  "pnpm-workspace.yaml",
  "turbo.json",
]);

const WEB_INPUT_PREFIXES = [
  "apps/web/",
  "packages/mcp-server/",
  "patches/",
];

export function isVercelWebInput(path) {
  return EXACT_WEB_INPUTS.has(path) ||
    WEB_INPUT_PREFIXES.some((prefix) => path.startsWith(prefix));
}

export function classifyVercelWebChanges(paths) {
  const relevant = paths.filter(isVercelWebInput);
  return { deploy: relevant.length > 0, relevant };
}

function changedPaths(before, after) {
  if (!/^[a-f0-9]{40}$/.test(after)) {
    throw new Error(`invalid after SHA: ${after}`);
  }
  if (!/^[a-f0-9]{40}$/.test(before) || /^0+$/.test(before)) {
    return ["apps/web/vercel.json"];
  }
  return execFileSync(
    "git",
    ["diff", "--name-only", "--diff-filter=ACDMRTUXB", before, after],
    { encoding: "utf8" },
  ).split("\n").filter(Boolean);
}

function main() {
  const [before, after] = process.argv.slice(2);
  if (!before || !after) {
    throw new Error("usage: classify-vercel-web-change.mjs <before> <after>");
  }
  const result = classifyVercelWebChanges(changedPaths(before, after));
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 2;
  }
}
