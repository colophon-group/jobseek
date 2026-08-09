#!/usr/bin/env node

import { appendFile, readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const DAY_MS = 24 * 60 * 60 * 1000;

function normalizeDirectory(directory) {
  return directory === "/." ? "/" : directory.replace(/\/$/, "") || "/";
}

function normalizeEcosystem(ecosystem) {
  if (ecosystem === "npm") return "npm_and_yarn";
  return ecosystem.replaceAll("-", "_");
}

function updateKey(ecosystem, directory) {
  return `${normalizeEcosystem(ecosystem)}:${normalizeDirectory(directory)}`;
}

export function parseDependabotConfig(source) {
  const updates = [];
  let current = null;

  for (const line of source.split("\n")) {
    const ecosystem = line.match(/^\s*-\s+package-ecosystem:\s+["']?([^"'\s]+)["']?\s*$/);
    if (ecosystem) {
      if (current?.directory) updates.push(current);
      current = { ecosystem: ecosystem[1], directory: null };
      continue;
    }

    if (!current) continue;
    const directory = line.match(/^\s+directory:\s+["']?([^"'\s]+)["']?\s*$/);
    if (directory) current.directory = normalizeDirectory(directory[1]);
  }

  if (current?.directory) updates.push(current);
  return updates;
}

export function parseVersionUpdateRun(run) {
  const match = run.name?.match(
    /^([a-z0-9_]+) in (\/\S*) - Update #[0-9]+$/,
  );
  if (!match) return null;
  return {
    ...run,
    ecosystem: match[1],
    directory: normalizeDirectory(match[2]),
    key: updateKey(match[1], match[2]),
  };
}

export function parseDependabotRun(run) {
  const fullUpdate = parseVersionUpdateRun(run);
  if (fullUpdate) return { ...fullUpdate, fullUpdate: true };

  const match = run.name?.match(
    /^([a-z0-9_]+) in (\/\S*) for (.+) - Update #[0-9]+$/,
  );
  if (!match) return null;
  return {
    ...run,
    ecosystem: match[1],
    directory: normalizeDirectory(match[2]),
    dependencies: match[3],
    fullUpdate: false,
  };
}

export function assessDependabotHealth({
  configuredUpdates,
  runs,
  alerts = [],
  alertsError = null,
  pullRequests = [],
  now = new Date(),
  staleUpdateDays = 9,
  stalePullRequestDays = 14,
}) {
  const parsedRuns = runs
    .map(parseVersionUpdateRun)
    .filter(Boolean)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const recentSecurityFailures = runs
    .map(parseDependabotRun)
    .filter(
      (run) =>
        run &&
        !run.fullUpdate &&
        run.conclusion !== "success" &&
        (now.getTime() - Date.parse(run.created_at)) / DAY_MS <= 2,
    );
  const problems = [];
  const updateRows = configuredUpdates.map(({ ecosystem, directory }) => {
    const key = updateKey(ecosystem, directory);
    const latest = parsedRuns.find((run) => run.key === key) ?? null;
    let status = "success";

    if (!latest) {
      status = "missing";
      problems.push(`No full update run was found for ${ecosystem} in ${directory}.`);
    } else {
      const ageDays = (now.getTime() - Date.parse(latest.created_at)) / DAY_MS;
      if (latest.conclusion !== "success") {
        status = "failed";
        problems.push(
          `Latest full update for ${ecosystem} in ${directory} concluded ${latest.conclusion ?? latest.status}.`,
        );
      } else if (ageDays > staleUpdateDays) {
        status = "stale";
        problems.push(
          `Latest full update for ${ecosystem} in ${directory} is ${Math.floor(ageDays)} days old.`,
        );
      }
    }

    return { ecosystem, directory, latest, status };
  });

  if (alertsError) {
    problems.push(`Dependabot alerts could not be read: ${alertsError}`);
  } else if (alerts.length > 0) {
    problems.push(`${alerts.length} Dependabot security alert(s) remain open.`);
  }
  if (recentSecurityFailures.length > 0 && (alertsError || alerts.length > 0)) {
    problems.push(
      `${recentSecurityFailures.length} recent Dependabot security update run(s) failed while alerts remain unresolved.`,
    );
  }

  const dependabotPullRequests = pullRequests.filter(
    (pullRequest) => pullRequest.user?.login === "dependabot[bot]",
  );
  const stalePullRequests = dependabotPullRequests.filter((pullRequest) => {
    const ageDays = (now.getTime() - Date.parse(pullRequest.created_at)) / DAY_MS;
    return ageDays > stalePullRequestDays;
  });
  if (stalePullRequests.length > 0) {
    problems.push(`${stalePullRequests.length} Dependabot PR(s) are stale.`);
  }

  return {
    healthy: problems.length === 0,
    problems,
    updateRows,
    alerts,
    alertsError,
    dependabotPullRequests,
    stalePullRequests,
    recentSecurityFailures,
    checkedAt: now.toISOString(),
  };
}

function escapeCell(value) {
  return String(value).replaceAll("|", "\\|").replaceAll("\n", " ");
}

function alertSummary(alert) {
  return (
    alert.security_advisory?.summary ??
    alert.dependency?.package?.name ??
    `Alert #${alert.number}`
  );
}

export function buildDependabotHealthReport(assessment) {
  const lines = [
    "<!-- dependabot-health -->",
    "# Dependabot health",
    "",
    `Checked: ${assessment.checkedAt}`,
    "",
    assessment.healthy
      ? "Status: healthy"
      : `Status: unhealthy (${assessment.problems.length} finding(s))`,
    "",
  ];

  if (assessment.problems.length > 0) {
    lines.push("## Findings", "");
    for (const problem of assessment.problems) lines.push(`- ${problem}`);
    lines.push("");
  }

  lines.push(
    "## Configured update cycles",
    "",
    "| Ecosystem | Directory | Latest full update | Status |",
    "| --- | --- | --- | --- |",
  );
  for (const row of assessment.updateRows) {
    const latest = row.latest
      ? `[${row.latest.created_at}](${row.latest.html_url})`
      : "not found";
    lines.push(
      `| ${escapeCell(row.ecosystem)} | \`${escapeCell(row.directory)}\` | ${latest} | ${row.status} |`,
    );
  }
  lines.push("");

  lines.push("## Open security alerts", "");
  if (assessment.alertsError) {
    lines.push(`- API error: ${assessment.alertsError}`);
  } else if (assessment.alerts.length === 0) {
    lines.push("None.");
  } else {
    for (const alert of assessment.alerts.slice(0, 50)) {
      const severity = alert.security_advisory?.severity ?? "unknown";
      const packageName = alert.dependency?.package?.name ?? "unknown package";
      lines.push(
        `- [#${alert.number}: ${alertSummary(alert)}](${alert.html_url}) (${severity}, ${packageName})`,
      );
    }
  }
  lines.push("");

  lines.push("## Recent failed security-update jobs", "");
  if (assessment.recentSecurityFailures.length === 0) {
    lines.push("None.");
  } else {
    for (const run of assessment.recentSecurityFailures) {
      lines.push(
        `- [${run.name}](${run.html_url}) concluded ${run.conclusion ?? run.status}.`,
      );
    }
    if (!assessment.alertsError && assessment.alerts.length === 0) {
      lines.push("", "The affected alerts are resolved on the default branch.");
    }
  }
  lines.push("");

  lines.push(
    "## Dependabot pull requests",
    "",
    `Open: ${assessment.dependabotPullRequests.length}; stale: ${assessment.stalePullRequests.length}.`,
    "",
  );
  for (const pullRequest of assessment.stalePullRequests) {
    lines.push(`- [#${pullRequest.number}: ${pullRequest.title}](${pullRequest.html_url})`);
  }

  return `${lines.join("\n").trim()}\n`;
}

function nextLink(header) {
  if (!header) return null;
  for (const part of header.split(",")) {
    const match = part.match(/<([^>]+)>;\s*rel="next"/);
    if (match) return match[1];
  }
  return null;
}

function apiClient({ token, baseUrl = "https://api.github.com" }) {
  const headers = {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "X-GitHub-Api-Version": "2022-11-28",
  };

  async function request(pathOrUrl, options = {}) {
    const url = pathOrUrl.startsWith("http") ? pathOrUrl : `${baseUrl}${pathOrUrl}`;
    const response = await fetch(url, {
      ...options,
      headers: {
        ...headers,
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      const body = await response.text();
      throw new Error(`${response.status} ${response.statusText}: ${body.slice(0, 500)}`);
    }
    if (response.status === 204) return { data: null, response };
    return { data: await response.json(), response };
  }

  async function paginate(path, arrayKey = null) {
    const values = [];
    let next = path;
    while (next) {
      const { data, response } = await request(next);
      const page = arrayKey ? data[arrayKey] : data;
      values.push(...page);
      next = nextLink(response.headers.get("link"));
    }
    return values;
  }

  return { request, paginate };
}

async function reconcileHealthIssue({ client, repository, assessment, report }) {
  const title = "[Dependabot] Automated health check failures";
  const openIssues = await client.paginate(
    `/repos/${repository}/issues?state=open&per_page=100`,
  );
  const existing = openIssues.find(
    (issue) => !issue.pull_request && issue.title === title,
  );

  if (assessment.healthy) {
    if (!existing) return;
    await client.request(`/repos/${repository}/issues/${existing.number}/comments`, {
      method: "POST",
      body: JSON.stringify({
        body: `Dependabot health recovered at ${assessment.checkedAt}. Closing the automated incident.`,
      }),
    });
    await client.request(`/repos/${repository}/issues/${existing.number}`, {
      method: "PATCH",
      body: JSON.stringify({ state: "closed", state_reason: "completed" }),
    });
    return;
  }

  if (existing) {
    await client.request(`/repos/${repository}/issues/${existing.number}`, {
      method: "PATCH",
      body: JSON.stringify({ body: report }),
    });
    return;
  }

  const assignee = process.env.DEPENDABOT_HEALTH_ASSIGNEE;
  await client.request(`/repos/${repository}/issues`, {
    method: "POST",
    body: JSON.stringify({
      title,
      body: report,
      labels: ["bug", "type:infra", "severity:medium"],
      assignees: assignee ? [assignee] : [],
    }),
  });
}

export async function main() {
  const token = process.env.GITHUB_TOKEN ?? process.env.GH_TOKEN;
  const repository = process.env.GITHUB_REPOSITORY;
  if (!token) throw new Error("GITHUB_TOKEN or GH_TOKEN is required");
  if (!repository) throw new Error("GITHUB_REPOSITORY is required");

  const now = new Date();
  const configuredUpdates = parseDependabotConfig(
    await readFile(".github/dependabot.yml", "utf8"),
  );
  const client = apiClient({ token, baseUrl: process.env.GITHUB_API_URL });
  const createdSince = new Date(now.getTime() - 21 * DAY_MS)
    .toISOString()
    .slice(0, 10);

  const [runs, pullRequests] = await Promise.all([
    client.paginate(
      `/repos/${repository}/actions/runs?event=dynamic&created=%3E%3D${createdSince}&per_page=100`,
      "workflow_runs",
    ),
    client.paginate(`/repos/${repository}/pulls?state=open&per_page=100`),
  ]);

  let alerts = [];
  let alertsError = null;
  try {
    alerts = await client.paginate(
      `/repos/${repository}/dependabot/alerts?state=open&per_page=100`,
    );
  } catch (error) {
    alertsError = error instanceof Error ? error.message : String(error);
  }

  const assessment = assessDependabotHealth({
    configuredUpdates,
    runs: runs.filter(
      (run) =>
        run.path === "dynamic/dependabot/dependabot-updates" &&
        run.actor?.login === "dependabot[bot]",
    ),
    alerts,
    alertsError,
    pullRequests,
    now,
  });
  const report = buildDependabotHealthReport(assessment);

  console.log(report);
  if (process.env.GITHUB_STEP_SUMMARY) {
    await appendFile(process.env.GITHUB_STEP_SUMMARY, report);
  }
  if (process.env.DEPENDABOT_HEALTH_DRY_RUN !== "1") {
    await reconcileHealthIssue({ client, repository, assessment, report });
  }

  if (!assessment.healthy) process.exitCode = 1;
}

const invokedDirectly =
  process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
