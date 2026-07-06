#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const rootDir = resolve(scriptDir, "..");
const rootPackage = JSON.parse(readFileSync(resolve(rootDir, "package.json"), "utf8"));
const packageManager = rootPackage.packageManager;

const match = /^pnpm@(.+)$/.exec(packageManager ?? "");
if (!match) {
  console.error("package.json must declare packageManager as pnpm@<version>");
  process.exit(1);
}

const result = spawnSync("npx", ["-y", `pnpm@${match[1]}`, ...process.argv.slice(2)], {
  cwd: rootDir,
  stdio: "inherit",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 1);
