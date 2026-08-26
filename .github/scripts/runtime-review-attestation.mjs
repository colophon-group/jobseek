#!/usr/bin/env node

const MARKER = "<!-- jobseek-runtime-review-event:v1 -->";
const SCHEMA = "jobseek.runtime-review-event/v1";
const STATUS_CONTEXT = "Runtime Review Attestation";
const BOT_LOGIN = "github-actions[bot]";
const SHA = /^[0-9a-f]{40}$/;

const GOVERNANCE_PATHS = new Set([
  ".github/rulesets/main-strict-gate.json",
  ".github/scripts/runtime-review-attestation.mjs",
  ".github/workflows/runtime-review-attestation.yml",
]);

export function isRuntimeMigrationPath(path) {
  if (GOVERNANCE_PATHS.has(path)) return true;
  if (!path.startsWith("apps/crawler/")) return false;
  return (
    path.startsWith("apps/crawler/contracts/") ||
    path.startsWith("apps/crawler/go/") ||
    path.startsWith("apps/crawler/cmd/") ||
    path.startsWith("apps/crawler/internal/") ||
    path.endsWith(".go") ||
    path.endsWith("/go.mod") ||
    path.endsWith("/go.sum")
  );
}

function exactKeys(value, expected) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  return (
    Object.keys(value).sort().join("\0") === [...expected].sort().join("\0")
  );
}

export function validateRecord(record) {
  const keys = [
    "approved_head_sha",
    "head_tree_sha",
    "kind",
    "outcome",
    "pull_request",
    "repair_count",
    "repaired_from_head",
    "repository",
    "review_sequence",
    "schema",
  ];
  if (!exactKeys(record, keys)) return "record fields are not exact";
  if (record.schema !== SCHEMA) return "record schema is unsupported";
  if (
    typeof record.repository !== "string" ||
    !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(record.repository)
  ) {
    return "record repository is invalid";
  }
  if (!Number.isSafeInteger(record.pull_request) || record.pull_request < 1) {
    return "record pull request is invalid";
  }
  if (!SHA.test(record.approved_head_sha) || !SHA.test(record.head_tree_sha)) {
    return "record commit or tree SHA is invalid";
  }
  if (record.kind === "head_observed") {
    if (
      record.outcome !== "invalidated" ||
      record.review_sequence !== null ||
      record.repair_count !== null ||
      record.repaired_from_head !== null
    ) {
      return "head observation has approval metadata";
    }
    return null;
  }
  if (record.kind !== "approval" || record.outcome !== "approved") {
    return "record outcome is not an approval or invalidation";
  }
  if (record.review_sequence === 1) {
    if (record.repair_count !== 0 || record.repaired_from_head !== null) {
      return "initial review has inconsistent repair metadata";
    }
    return null;
  }
  if (
    record.review_sequence === 2 &&
    record.repair_count === 1 &&
    SHA.test(record.repaired_from_head) &&
    record.repaired_from_head !== record.approved_head_sha
  ) {
    return null;
  }
  return "record exceeds the one-review/one-repair policy";
}

export function parseEventComments(comments) {
  const events = [];
  for (const comment of comments) {
    if (!comment.body?.startsWith(`${MARKER}\n`)) continue;
    if (comment.user?.login !== BOT_LOGIN) {
      return {
        error: "record marker was not published by the trusted workflow",
      };
    }
    if (comment.created_at !== comment.updated_at) {
      return { error: "a machine record was edited" };
    }
    let record;
    try {
      record = JSON.parse(comment.body.slice(MARKER.length + 1));
    } catch {
      return { error: "machine record contains malformed JSON" };
    }
    const error = validateRecord(record);
    if (error) return { error };
    events.push({ id: Number(comment.id), record });
  }
  events.sort((left, right) => left.id - right.id);
  return { events };
}

export function validateTransition(previous, next) {
  const error = validateRecord(next);
  if (error) return error;
  if (next.kind !== "approval") return "transition target is not an approval";
  if (!previous) return null;
  if (
    previous.repository !== next.repository ||
    previous.pull_request !== next.pull_request
  ) {
    return "approval transition crosses repository or pull request";
  }
  if (previous.approved_head_sha === next.approved_head_sha) {
    return "an approval already exists for this exact head";
  }
  if (next.repair_count < previous.repair_count) {
    return "approval transition rolls the repair count back";
  }
  if (previous.repair_count === 0 && next.repair_count === 0) {
    return previous.head_tree_sha === next.head_tree_sha
      ? null
      : "changed content requires the one bounded repair";
  }
  if (previous.repair_count === 0 && next.repair_count === 1) {
    return next.repaired_from_head === previous.approved_head_sha
      ? null
      : "repair predecessor does not match the previously approved head";
  }
  if (previous.repair_count === 1 && next.repair_count === 1) {
    if (previous.repaired_from_head !== next.repaired_from_head) {
      return "approval transition represents a second repair";
    }
    return previous.head_tree_sha === next.head_tree_sha
      ? null
      : "content changed after the repair budget was spent";
  }
  return "approval transition exceeds the one-repair budget";
}

function validateHistory(events) {
  let priorApproval = null;
  for (const event of events) {
    if (event.record.kind !== "approval") continue;
    const error = validateTransition(priorApproval, event.record);
    if (error) return error;
    priorApproval = event.record;
  }
  return null;
}

export function evaluateApproval({
  repository,
  pullRequest,
  headAtStart,
  treeAtStart,
  headAtConclusion,
  comments,
  changedPaths,
}) {
  if (!changedPaths.some(isRuntimeMigrationPath)) {
    return {
      eligible: true,
      scoped: false,
      reason: "not a runtime migration PR",
    };
  }
  if (headAtConclusion !== headAtStart) {
    return {
      eligible: false,
      scoped: true,
      reason: "PR head changed while the approval gate was evaluating",
    };
  }
  const parsed = parseEventComments(comments);
  if (parsed.error)
    return { eligible: false, scoped: true, reason: parsed.error };
  if (parsed.events.length === 0) {
    return {
      eligible: false,
      scoped: true,
      reason: "approval record is missing",
    };
  }
  const historyError = validateHistory(parsed.events);
  if (historyError) {
    return { eligible: false, scoped: true, reason: historyError };
  }
  const latest = parsed.events.at(-1).record;
  if (latest.kind !== "approval") {
    return {
      eligible: false,
      scoped: true,
      reason: "the latest head observation has no current approval",
    };
  }
  const sameHeadApprovals = parsed.events.filter(
    (event) =>
      event.record.kind === "approval" &&
      event.record.approved_head_sha === latest.approved_head_sha,
  );
  if (sameHeadApprovals.length !== 1) {
    return {
      eligible: false,
      scoped: true,
      reason: "multiple or conflicting approval records target the same head",
    };
  }
  if (latest.repository !== repository || latest.pull_request !== pullRequest) {
    return {
      eligible: false,
      scoped: true,
      reason: "approval record targets another repository or pull request",
    };
  }
  if (latest.approved_head_sha !== headAtStart) {
    return {
      eligible: false,
      scoped: true,
      reason:
        latest.head_tree_sha === treeAtStart
          ? "approval is stale: current commit changed even though its tree is identical"
          : "approval is stale: current commit changed",
    };
  }
  if (latest.head_tree_sha !== treeAtStart) {
    return {
      eligible: false,
      scoped: true,
      reason: "approval tree diagnostic does not match the approved commit",
    };
  }
  return {
    eligible: true,
    scoped: true,
    reason: `exact head approved at review sequence ${latest.review_sequence}`,
  };
}

function body(record) {
  return `${MARKER}\n${JSON.stringify(record)}`;
}

function requiredEnv(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

async function api(path, options = {}) {
  const response = await fetch(`${requiredEnv("GITHUB_API_URL")}${path}`, {
    ...options,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${requiredEnv("GH_TOKEN")}`,
      "X-GitHub-Api-Version": "2022-11-28",
      ...(options.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(
      `GitHub API ${options.method ?? "GET"} ${path} returned ${response.status}`,
    );
  }
  if (response.status === 204) return null;
  return response.json();
}

async function paginate(path) {
  const values = [];
  for (let page = 1; ; page += 1) {
    const separator = path.includes("?") ? "&" : "?";
    const batch = await api(`${path}${separator}per_page=100&page=${page}`);
    if (!Array.isArray(batch))
      throw new Error(`${path} did not return an array`);
    values.push(...batch);
    if (batch.length < 100) return values;
  }
}

async function pullState(repository, pullRequest) {
  const pull = await api(`/repos/${repository}/pulls/${pullRequest}`);
  if (pull.state !== "open" || !SHA.test(pull.head?.sha)) {
    throw new Error(`PR #${pullRequest} is not open or has an invalid head`);
  }
  return pull;
}

async function treeFor(repository, head) {
  const commit = await api(`/repos/${repository}/git/commits/${head}`);
  if (!SHA.test(commit.tree?.sha))
    throw new Error(`commit ${head} has no valid tree`);
  return commit.tree.sha;
}

async function commentsFor(repository, pullRequest) {
  return paginate(`/repos/${repository}/issues/${pullRequest}/comments`);
}

async function pathsFor(repository, pullRequest) {
  const files = await paginate(
    `/repos/${repository}/pulls/${pullRequest}/files`,
  );
  return files.flatMap((file) =>
    [file.filename, file.previous_filename].filter(
      (path) => typeof path === "string" && path.length > 0,
    ),
  );
}

async function appendRecord(repository, pullRequest, record) {
  await api(`/repos/${repository}/issues/${pullRequest}/comments`, {
    method: "POST",
    body: JSON.stringify({ body: body(record) }),
    headers: { "Content-Type": "application/json" },
  });
}

async function observeCommand() {
  const repository = requiredEnv("REPO");
  const pullRequest = Number(requiredEnv("PR"));
  const first = await pullState(repository, pullRequest);
  const tree = await treeFor(repository, first.head.sha);
  const final = await pullState(repository, pullRequest);
  if (final.head.sha !== first.head.sha) {
    throw new Error("PR head changed while recording synchronization");
  }
  await appendRecord(repository, pullRequest, {
    schema: SCHEMA,
    kind: "head_observed",
    repository,
    pull_request: pullRequest,
    approved_head_sha: first.head.sha,
    head_tree_sha: tree,
    outcome: "invalidated",
    review_sequence: null,
    repair_count: null,
    repaired_from_head: null,
  });
}

async function publishStatus(repository, head, result) {
  await api(`/repos/${repository}/statuses/${head}`, {
    method: "POST",
    body: JSON.stringify({
      state: result.eligible ? "success" : "failure",
      context: STATUS_CONTEXT,
      description: result.reason.slice(0, 140),
      target_url: requiredEnv("TARGET_URL"),
    }),
    headers: { "Content-Type": "application/json" },
  });
}

async function checkPull(repository, pullRequest) {
  const initial = await pullState(repository, pullRequest);
  const headAtStart = initial.head.sha;
  const [treeAtStart, comments, changedPaths] = await Promise.all([
    treeFor(repository, headAtStart),
    commentsFor(repository, pullRequest),
    pathsFor(repository, pullRequest),
  ]);
  const final = await pullState(repository, pullRequest);
  const result = evaluateApproval({
    repository,
    pullRequest,
    headAtStart,
    treeAtStart,
    headAtConclusion: final.head.sha,
    comments,
    changedPaths,
  });
  if (final.head.sha !== headAtStart) {
    await publishStatus(repository, headAtStart, {
      eligible: false,
      reason: result.reason,
    });
  }
  await publishStatus(repository, final.head.sha, result);
  console.log(
    `PR #${pullRequest}: ${result.eligible ? "PASS" : "BLOCK"}: ${result.reason}`,
  );
}

async function checkCommand() {
  const repository = requiredEnv("REPO");
  if (process.env.PR) {
    await checkPull(repository, Number(process.env.PR));
    return;
  }
  const pulls = await paginate(`/repos/${repository}/pulls?state=open`);
  for (const pull of pulls) await checkPull(repository, pull.number);
}

async function publishCommand() {
  const repository = requiredEnv("REPO");
  const pullRequest = Number(requiredEnv("PR"));
  const expectedHead = requiredEnv("EXPECTED_HEAD_SHA");
  if (requiredEnv("REF_NAME") !== requiredEnv("DEFAULT_BRANCH")) {
    throw new Error("approval publication must run from the default branch");
  }
  if (
    !Number.isSafeInteger(pullRequest) ||
    pullRequest < 1 ||
    !SHA.test(expectedHead)
  ) {
    throw new Error("PR number or expected head is invalid");
  }
  const pull = await pullState(repository, pullRequest);
  if (pull.head.sha !== expectedHead) {
    throw new Error(
      `expected head ${expectedHead} is stale; live head is ${pull.head.sha}`,
    );
  }
  const tree = await treeFor(repository, expectedHead);
  const repairedFrom = process.env.REPAIRED_FROM_HEAD?.trim() || null;
  const record = {
    schema: SCHEMA,
    kind: "approval",
    repository,
    pull_request: pullRequest,
    approved_head_sha: expectedHead,
    head_tree_sha: tree,
    outcome: "approved",
    review_sequence: Number(requiredEnv("REVIEW_SEQUENCE")),
    repair_count: Number(requiredEnv("REPAIR_COUNT")),
    repaired_from_head: repairedFrom,
  };
  const error = validateRecord(record);
  if (error) throw new Error(error);
  const parsed = parseEventComments(await commentsFor(repository, pullRequest));
  if (parsed.error) throw new Error(parsed.error);
  const approvals = parsed.events.filter(
    (event) => event.record.kind === "approval",
  );
  if (
    approvals.some((event) => event.record.approved_head_sha === expectedHead)
  ) {
    throw new Error(
      "a prior approval for this exact head cannot be reactivated",
    );
  }
  const transitionError = validateTransition(
    approvals.at(-1)?.record ?? null,
    record,
  );
  if (transitionError) throw new Error(transitionError);
  const final = await pullState(repository, pullRequest);
  if (final.head.sha !== expectedHead) {
    throw new Error("PR head changed before approval publication");
  }
  await appendRecord(repository, pullRequest, record);
  console.log(
    `Published exact-head approval for PR #${pullRequest} at ${expectedHead}`,
  );
}

async function main() {
  if (process.argv[2] === "observe") return observeCommand();
  if (process.argv[2] === "check") return checkCommand();
  if (process.argv[2] === "publish") return publishCommand();
  throw new Error(
    "usage: runtime-review-attestation.mjs <observe|check|publish>",
  );
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(`ERROR: ${error.message}`);
    process.exitCode = 1;
  });
}

export { BOT_LOGIN, MARKER, SCHEMA };
