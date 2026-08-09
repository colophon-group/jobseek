import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  assessDependabotHealth,
  buildDependabotHealthReport,
  parseDependabotConfig,
  parseDependabotRun,
  parseVersionUpdateRun,
} from "../.github/scripts/dependabot-health.mjs";

const config = readFileSync(".github/dependabot.yml", "utf8");
const workflow = readFileSync(".github/workflows/dependabot-health.yml", "utf8");

function successfulRun(ecosystem, directory, id) {
  const runDirectory = directory === "/" ? "/." : directory;
  return {
    id,
    name: `${ecosystem} in ${runDirectory} - Update #${id}`,
    status: "completed",
    conclusion: "success",
    created_at: "2026-08-08T08:00:00Z",
    html_url: `https://github.example/runs/${id}`,
  };
}

test("Dependabot config monitors every configured ecosystem and directory", () => {
  const updates = parseDependabotConfig(config);
  assert.deepEqual(updates, [
    { ecosystem: "github-actions", directory: "/" },
    { ecosystem: "npm", directory: "/" },
    { ecosystem: "uv", directory: "/apps/crawler" },
    { ecosystem: "uv", directory: "/apps/crawler/ws-package" },
    { ecosystem: "uv", directory: "/apps/web/script" },
    { ecosystem: "docker", directory: "/apps/web" },
    { ecosystem: "docker", directory: "/apps/crawler" },
    { ecosystem: "docker-compose", directory: "/" },
    { ecosystem: "docker-compose", directory: "/apps/crawler" },
  ]);
});

test("version run parser ignores dependency-specific security and rebase jobs", () => {
  assert.equal(
    parseVersionUpdateRun({
      name: "npm_and_yarn in /. for next, postcss - Update #123",
    }),
    null,
  );
  assert.deepEqual(
    parseDependabotRun({
      name: "npm_and_yarn in /. for next, postcss - Update #123",
      conclusion: "failure",
      created_at: "2026-08-08T08:00:00Z",
    }),
    {
      name: "npm_and_yarn in /. for next, postcss - Update #123",
      conclusion: "failure",
      created_at: "2026-08-08T08:00:00Z",
      ecosystem: "npm_and_yarn",
      directory: "/",
      dependencies: "next, postcss",
      fullUpdate: false,
    },
  );
  assert.deepEqual(
    parseVersionUpdateRun({
      name: "npm_and_yarn in /. - Update #456",
      created_at: "2026-08-08T08:00:00Z",
    }),
    {
      name: "npm_and_yarn in /. - Update #456",
      created_at: "2026-08-08T08:00:00Z",
      ecosystem: "npm_and_yarn",
      directory: "/",
      key: "npm_and_yarn:/",
    },
  );
});

test("healthy assessment requires fresh successful cycles and no alerts", () => {
  const configuredUpdates = parseDependabotConfig(config);
  const ecosystemNames = {
    "github-actions": "github_actions",
    npm: "npm_and_yarn",
    uv: "uv",
    docker: "docker",
    "docker-compose": "docker_compose",
  };
  const runs = configuredUpdates.map((update, index) =>
    successfulRun(ecosystemNames[update.ecosystem], update.directory, index + 1),
  );
  const assessment = assessDependabotHealth({
    configuredUpdates,
    runs,
    now: new Date("2026-08-09T08:00:00Z"),
  });
  assert.equal(assessment.healthy, true);
  assert.deepEqual(assessment.problems, []);
});

test("failed cycles, open alerts, and stale PRs are reported together", () => {
  const configuredUpdates = [{ ecosystem: "npm", directory: "/" }];
  const failedRun = {
    ...successfulRun("npm_and_yarn", "/", 1),
    conclusion: "failure",
  };
  const assessment = assessDependabotHealth({
    configuredUpdates,
    runs: [
      failedRun,
      {
        name: "npm_and_yarn in /. for example - Update #2",
        conclusion: "failure",
        created_at: "2026-08-08T09:00:00Z",
        html_url: "https://github.example/runs/2",
      },
    ],
    alerts: [
      {
        number: 42,
        html_url: "https://github.example/alerts/42",
        dependency: { package: { name: "example" } },
        security_advisory: { severity: "high", summary: "Example advisory" },
      },
    ],
    pullRequests: [
      {
        number: 7,
        title: "Bump example",
        html_url: "https://github.example/pulls/7",
        created_at: "2026-07-01T00:00:00Z",
        user: { login: "dependabot[bot]" },
      },
    ],
    now: new Date("2026-08-09T08:00:00Z"),
  });

  assert.equal(assessment.healthy, false);
  assert.equal(assessment.problems.length, 4);
  const report = buildDependabotHealthReport(assessment);
  assert.match(report, /Latest full update for npm in \/ concluded failure/);
  assert.match(report, /1 Dependabot security alert\(s\) remain open/);
  assert.match(report, /security update run\(s\) failed while alerts remain unresolved/);
  assert.match(report, /npm_and_yarn in \/\. for example - Update #2/);
  assert.match(report, /#42: Example advisory/);
  assert.match(report, /#7: Bump example/);
});

test("a repaired default branch clears old partial failures when alerts are gone", () => {
  const assessment = assessDependabotHealth({
    configuredUpdates: [{ ecosystem: "npm", directory: "/" }],
    runs: [
      successfulRun("npm_and_yarn", "/", 1),
      {
        name: "npm_and_yarn in /. for example - Update #2",
        conclusion: "failure",
        created_at: "2026-08-08T09:00:00Z",
        html_url: "https://github.example/runs/2",
      },
    ],
    now: new Date("2026-08-09T08:00:00Z"),
  });

  assert.equal(assessment.healthy, true);
  assert.equal(assessment.recentSecurityFailures.length, 1);
  assert.match(
    buildDependabotHealthReport(assessment),
    /affected alerts are resolved on the default branch/,
  );
});

test("health workflow uses least-privilege read access plus issue reporting", () => {
  assert.match(workflow, /actions: read/);
  assert.match(workflow, /contents: read/);
  assert.match(workflow, /pull-requests: read/);
  assert.match(workflow, /security-events: read/);
  assert.match(workflow, /issues: write/);
  assert.doesNotMatch(workflow, /contents: write/);
  assert.match(workflow, /cron: "15 8 \* \* \*"/);
});
