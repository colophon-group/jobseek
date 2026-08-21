import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const repoRoot = process.cwd();
const docsDir = path.join(repoRoot, "docs");
const readmePath = path.join(docsDir, "README.md");
const readme = readFileSync(readmePath, "utf8");

function relative(filePath) {
  return path.relative(repoRoot, filePath);
}

function markdownFiles(dir) {
  return readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".md"))
    .map((entry) => path.join(dir, entry.name))
    .sort((a, b) => a.localeCompare(b));
}

test("docs README indexes every top-level docs markdown file", () => {
  const docs = markdownFiles(docsDir)
    .map((filePath) => path.basename(filePath))
    .filter((name) => name !== "README.md");

  for (const doc of docs) {
    assert.ok(
      readme.includes(`](${doc})`),
      `docs/README.md should link to ${doc}`,
    );
  }
});

test("docs README indexes every ADR", () => {
  const adrDir = path.join(docsDir, "adr");
  const adrs = markdownFiles(adrDir).map((filePath) => path.basename(filePath));

  for (const adr of adrs) {
    assert.ok(
      readme.includes(`](adr/${adr})`),
      `docs/README.md should link to adr/${adr}`,
    );
  }
});

test("docs README and ADR relative markdown links resolve", () => {
  const checkedFiles = [
    readmePath,
    path.join(docsDir, "00-overview.md"),
    ...markdownFiles(path.join(docsDir, "adr")),
  ];

  for (const filePath of checkedFiles) {
    const source = readFileSync(filePath, "utf8");
    const linkPattern = /\[[^\]]+\]\(([^)]+)\)/g;

    for (const match of source.matchAll(linkPattern)) {
      const target = match[1].trim();
      if (
        target.startsWith("#") ||
        /^[a-z][a-z0-9+.-]*:/i.test(target)
      ) {
        continue;
      }

      const [targetPath] = target.split("#");
      if (!targetPath) continue;

      const resolved = path.resolve(path.dirname(filePath), targetPath);
      assert.ok(
        existsSync(resolved),
        `${relative(filePath)} links to missing target ${target}`,
      );
    }
  }
});

test("production Codex guidance keeps scheduling on Hetzner", () => {
  const guidancePaths = [
    "AGENTS.md",
    "docs/00-overview.md",
    "docs/01-agent-workflow.md",
    "docs/16-hetzner-maintenance.md",
    "docs/18-codex-automation-deployment.md",
    "docs/README.md",
  ];
  const staleWording = [
    /Codex\s+desktop/i,
    /desktop[- ]scheduler/i,
    /Hetzner(?:-hosted)?\s+local\s+Codex/i,
  ];
  const retiredRoutineNames = [
    ["jobseek", "company", "request", "resolver"].join("-"),
    ["jobseek", "daily", "classifications"].join("-"),
    ["jobseek", "daily", "error", "review"].join("-"),
  ];

  for (const guidancePath of guidancePaths) {
    const source = readFileSync(guidancePath, "utf8");
    for (const pattern of staleWording) {
      assert.doesNotMatch(
        source,
        pattern,
        `${guidancePath} must identify the Hetzner scheduler unambiguously`,
      );
    }
    for (const retiredName of retiredRoutineNames) {
      assert.ok(
        !source.includes(retiredName),
        `${guidancePath} must not reference retired routine ${retiredName}`,
      );
    }
  }

  const runbook = readFileSync(
    "docs/18-codex-automation-deployment.md",
    "utf8",
  );
  for (const unit of [
    "jobseek-codex-governor.timer",
    "jobseek-codex-daily-annotations.timer",
    "jobseek-codex-daily-error-review.timer",
    "jobseek-codex-docker-lifecycle.service",
  ]) {
    assert.ok(runbook.includes(`\`${unit}\``), `runner inventory lists ${unit}`);
  }
  assert.match(runbook, /only\s+production scheduling surface/i);
  assert.match(runbook, /GitHub Actions or workstation schedules/i);
});
