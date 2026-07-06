#!/usr/bin/env node
import { readFileSync } from "node:fs";

const packageJson = JSON.parse(readFileSync("package.json", "utf8"));
const workspaceYaml = readFileSync("pnpm-workspace.yaml", "utf8");
const preCommitConfig = readFileSync(".pre-commit-config.yaml", "utf8");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

assert(packageJson.packageManager === "pnpm@10.30.0", "packageManager must pin pnpm@10.30.0");
assert(!("pnpm" in packageJson), "pnpm settings must live in pnpm-workspace.yaml, not package.json");
assert(workspaceYaml.includes("\noverrides:\n"), "pnpm-workspace.yaml must define overrides");
assert(
  workspaceYaml.includes("\nonlyBuiltDependencies:\n"),
  "pnpm-workspace.yaml must define onlyBuiltDependencies",
);
assert(
  workspaceYaml.includes("brace-expansion: 5.0.6"),
  "pnpm-workspace.yaml must carry security override entries",
);
assert(
  workspaceYaml.includes("  - esbuild") && workspaceYaml.includes("  - sharp"),
  "pnpm-workspace.yaml must allow required native build dependencies",
);
assert(
  workspaceYaml.includes("\nallowBuilds:\n") &&
    workspaceYaml.includes("  esbuild: true") &&
    workspaceYaml.includes("  sharp: true"),
  "pnpm-workspace.yaml must explicitly approve pnpm 11 native builds",
);
assert(
  preCommitConfig.includes("node scripts/pnpm-pinned.mjs --dir apps/web lint"),
  "web-lint pre-commit hook must use the pinned pnpm wrapper",
);
