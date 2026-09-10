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
  const guidanceContracts = new Map([
    [".agents/skills/jobseek-error-review/SKILL.md", /Hetzner Codex runner/i],
    ["AGENTS.md", /Hetzner Codex runner is the production scheduler/i],
    ["apps/crawler/AGENTS.md", /Hetzner Codex runner\s+schedules recurring production routines/i],
    ["docs/00-overview.md", /Hetzner-hosted Codex runner is the recurring/i],
    ["docs/01-agent-workflow.md", /primary recurring path is the Hetzner-hosted Codex runner/i],
    ["docs/14-error-review-routine.md", /jobseek-codex-daily-error-review\.timer/],
    ["docs/15-data-sampling-routine.md", /jobseek-codex-daily-annotations\.timer/],
    ["docs/16-hetzner-maintenance.md", /systemctl is-active jobseek-codex-daily-error-review\.timer/],
    ["docs/17-codex-migration-verification-runbook.md", /daily routines run through the Hetzner\s+Codex runner/i],
    ["docs/18-codex-automation-deployment.md", /only\s+production scheduling surface/i],
    ["docs/README.md", /Hetzner Codex Runner Deployment/],
  ]);

  for (const [guidancePath, contract] of guidanceContracts) {
    const source = readFileSync(guidancePath, "utf8");
    assert.match(source, contract, `${guidancePath} states its active scheduling contract`);
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
  assert.match(runbook, /actual Codex execution to the Hetzner timers/i);

  const deployWorkflow = readFileSync(
    ".github/workflows/deploy-codex-runner.yml",
    "utf8",
  );
  assert.match(deployWorkflow, /^name: Deploy Codex Runner \(Hetzner\)$/m);
  assert.match(deployWorkflow, /branches: \[main\]/);
  assert.match(deployWorkflow, /JOBSEEK_CODEX_START_TIMERS=0/);
});
