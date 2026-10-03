/** Aggregate-only observation. Raw Vercel request records stay in bounded memory. */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { writeFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { inflateRawSync } from 'node:zlib';

export const CLI_VERSION = '62.1.0';
export const QUARTER = 15 * 60_000;
export const OPERATIONS = new Set(['create', 'handoff', 'update', 'copy', 'copy_shared', 'add', 'remove', 'clear', 'star']);
export const OUTCOMES = new Set(['success', 'invalid_input', 'lookup_miss', 'lookup_unavailable', 'identity_conflict', 'not_found_or_forbidden', 'limit', 'rate_limited', 'unauthenticated', 'database_foreign_key', 'database_error', 'unexpected_failure', 'rejected']);
export const REASONS = new Set(['invalid_request_timestamp', 'missing_request_identity', 'duplicate_request_rows', 'unsupported_log_schema', 'possible_provider_request_line_cap', 'truncated_request_message', 'unparsed_candidate_message', 'unrecognized_telemetry_contract', 'possible_provider_request_byte_cap', 'matching_request_without_parseable_event', 'malformed_request_record', 'query_budget_exhausted', 'query_timeout', 'query_transport_budget_exhausted', 'unexpected_cli_row_limit', 'cli_query_failed', 'saturated_minimum_window', 'saturated_query_budget', 'retention_boundary', 'replay_count_regression']);
const EVENT = 'company_selection_mutation';
const WORKFLOW = '.github/workflows/company-selection-observation.yml';
const OWNER = 'viktor-shcherb';
const RETENTION = { hobby: 3600_000, pro: 24 * 3600_000, enterprise: 72 * 3600_000 };
const MAX_ARTIFACT = 4 * 1024 * 1024;
const MAX_HISTORY = 680;
const iso = value => new Date(value).toISOString();
const assert = condition => { if (!condition) throw new Error('invalid_observation_contract'); };
const exactKeys = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
const validTime = value => typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?Z$/.test(value) && Number.isFinite(Date.parse(value));
const safeNumber = value => Number.isSafeInteger(value) && value >= 0;
export const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const empty = () => new Map();
const key = (operation, outcome) => `${operation}:${outcome}`;
const add = (counts, operation, outcome, amount = 1) => counts.set(key(operation, outcome), (counts.get(key(operation, outcome)) ?? 0) + amount);
export const countRows = counts => [...counts].sort().map(([pair, count]) => { const [operation, outcome] = pair.split(':'); return { operation, outcome, count }; });
export function parseCounts(rows) {
  assert(Array.isArray(rows) && rows.length <= OPERATIONS.size * OUTCOMES.size);
  const counts = empty();
  for (const row of rows) {
    assert(exactKeys(row, ['operation', 'outcome', 'count']) && OPERATIONS.has(row.operation) && OUTCOMES.has(row.outcome) && safeNumber(row.count) && row.count > 0 && !counts.has(key(row.operation, row.outcome)));
    add(counts, row.operation, row.outcome, row.count);
  }
  return counts;
}
function mergeCounts(left, right, maximum = false) {
  const result = new Map(left);
  for (const [pair, count] of right) result.set(pair, maximum ? Math.max(count, result.get(pair) ?? 0) : count + (result.get(pair) ?? 0));
  return result;
}

export function parseRequest(raw, start, end, seen, result) {
  result.rows++;
  try {
    const row = JSON.parse(raw);
    if (typeof row.timestamp !== 'number' || !Number.isFinite(row.timestamp)) { result.reasons.add('invalid_request_timestamp'); return; }
    if (row.timestamp < start || row.timestamp >= end) return;
    if (typeof row.id !== 'string' || !row.id || row.id.length > 1024) { result.reasons.add('missing_request_identity'); return; }
    if (seen.has(row.id)) { result.reasons.add('duplicate_request_rows'); return; }
    seen.add(row.id); // Only transient request identity for deduplication. Never serialize.
    if (!Array.isArray(row.logs)) { result.reasons.add('unsupported_log_schema'); return; }
    if (row.logs.length >= 256) result.reasons.add('possible_provider_request_line_cap');
    let found = 0, bytes = 0;
    for (const log of row.logs) {
      if (!log || typeof log.message !== 'string') { result.reasons.add('unsupported_log_schema'); continue; }
      const size = Buffer.byteLength(log.message);
      bytes += size;
      if (log.messageTruncated || size >= 256 * 1024) result.reasons.add('truncated_request_message');
      let event;
      try { event = JSON.parse(log.message); }
      catch { if (log.message.includes(EVENT)) result.reasons.add('unparsed_candidate_message'); continue; }
      if (!event || typeof event !== 'object' || event.event !== EVENT) continue;
      if (!exactKeys(event, ['event', 'operation', 'outcome']) || !OPERATIONS.has(event.operation) || !OUTCOMES.has(event.outcome)) { result.reasons.add('unrecognized_telemetry_contract'); continue; }
      add(result.counts, event.operation, event.outcome);
      found++;
    }
    if (bytes >= 1024 * 1024) result.reasons.add('possible_provider_request_byte_cap');
    if (!found) result.reasons.add('matching_request_without_parseable_event');
    // Top-level message is a selected display log, so it must NEVER be counted again.
  } catch { result.reasons.add('malformed_request_record'); }
}
const queryResult = () => ({ rows: 0, counts: empty(), reasons: new Set() });

/** Bounded private subprocess capture. Child stderr and errors never escape. */
export function capture(command, args, { env = {}, maxBytes = 1024 * 1024, timeout = 30_000, onLine } = {}) {
  return new Promise(resolve => {
    const child = spawn(command, args, { env: { PATH: process.env.PATH, HOME: process.env.HOME, TMPDIR: process.env.TMPDIR, CI: 'true', NO_COLOR: '1', ...env }, stdio: ['ignore', 'pipe', 'ignore'], detached: process.platform !== 'win32' });
    let bytes = 0, chunks = [], pending = Buffer.alloc(0), reason, settled = false;
    const kill = why => {
      reason ??= why;
      try { if (process.platform !== 'win32') process.kill(-child.pid, 'SIGKILL'); else child.kill('SIGKILL'); } catch { /* already exited */ }
    };
    const timer = setTimeout(() => kill('query_timeout'), timeout);
    child.stdout.on('data', chunk => {
      bytes += chunk.length;
      if (bytes > maxBytes) { kill('query_transport_budget_exhausted'); return; }
      if (!onLine) { chunks.push(chunk); return; }
      pending = Buffer.concat([pending, chunk]);
      if (pending.length > 8 * 1024 * 1024) { kill('query_transport_budget_exhausted'); return; }
      let newline;
      while ((newline = pending.indexOf(10)) !== -1) {
        const line = pending.subarray(0, newline);
        pending = pending.subarray(newline + 1);
        if (line.length) { try { onLine(line); } catch { kill('cli_query_failed'); } }
      }
    });
    const done = code => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (onLine && pending.length && !reason) { try { onLine(pending); } catch { reason = 'cli_query_failed'; } }
      resolve({ ok: code === 0 && !reason, reason: reason ?? (code === 0 ? null : 'cli_query_failed'), data: Buffer.concat(chunks) });
    };
    child.on('error', () => done(1));
    child.on('close', done);
  });
}
function vercelRunner(local = false) {
  return args => local ? ['/opt/homebrew/bin/vercel', args] : ['pnpm', ['dlx', `vercel@${CLI_VERSION}`, ...args]];
}
export function logsArgs({ project, scope, start, end, limit }) {
  return ['logs', '--project', project, '--scope', scope, '--environment', 'production', '--since', iso(start), '--until', iso(end), '--query', EVENT, '--limit', String(limit), '--json', '--no-color'];
}
export function collector({ run, project, scope, retention, now = Date.now, limit = 1000, deadline = Date.now() + 120_000, maxQueries = 24, queryOverride } = {}) {
  let remaining = maxQueries;
  async function query(start, end) {
    const result = queryResult();
    if (remaining-- <= 0 || now() >= deadline) { result.reasons.add('query_budget_exhausted'); return result; }
    if (start < now() - retention + 120_000) { result.reasons.add('retention_boundary'); return result; }
    if (queryOverride) return queryOverride(start, end);
    const seen = new Set();
    const [command, args] = run(logsArgs({ project, scope, start, end, limit }));
    const output = await capture(command, args, { env: { VERCEL_TOKEN: process.env.VERCEL_TOKEN }, maxBytes: 64 * 1024 * 1024, timeout: Math.min(30_000, Math.max(1, deadline - now())), onLine: line => parseRequest(line.toString('utf8'), start, end, seen, result) });
    if (!output.ok) result.reasons.add(output.reason);
    if (result.rows > limit) result.reasons.add('unexpected_cli_row_limit');
    if (start < now() - retention + 120_000) result.reasons.add('retention_boundary');
    return result;
  }
  async function window(start, end) {
    const result = await query(start, end);
    if (result.rows >= limit) {
      if (end - start <= 1000) result.reasons.add('saturated_minimum_window');
      else if (remaining < 2 || now() >= deadline) result.reasons.add('saturated_query_budget');
      else {
        const middle = Math.floor((start + end) / 2);
        const left = await window(start, middle), right = await window(middle, end);
        result.counts = mergeCounts(left.counts, right.counts);
        result.reasons = new Set([...left.reasons, ...right.reasons]);
      }
    }
    return result;
  }
  return { window, budget: () => remaining > 0 && now() < deadline };
}

export function validateRun(run, now = Date.now()) {
  assert(run && run.status === 'completed' && run.head_branch === 'main' && run.path === WORKFLOW && ['schedule', 'workflow_dispatch'].includes(run.event) && /^[a-f0-9]{40}$/.test(run.head_sha) && safeNumber(run.id) && run.id > 0 && safeNumber(run.run_attempt) && run.run_attempt > 0 && validTime(run.updated_at) && Date.parse(run.updated_at) <= now + 60_000 && Date.parse(run.updated_at) >= now - 8 * 86400_000);
  if (run.event === 'workflow_dispatch') assert(run.actor?.login === OWNER && run.triggering_actor?.login === OWNER);
}
export function validateArtifact(artifact, run) {
  assert(artifact && safeNumber(artifact.id) && artifact.id > 0 && !artifact.expired && artifact.name === `company-selection-checkpoints-${run.id}-${run.run_attempt}` && safeNumber(artifact.size_in_bytes) && artifact.size_in_bytes > 0 && artifact.size_in_bytes <= MAX_ARTIFACT && /^sha256:[a-f0-9]{64}$/.test(artifact.digest));
}
export function validateSnapshot(envelope, run, observationStart, now = Date.now()) {
  assert(exactKeys(envelope, ['body', 'sha256']) && /^[a-f0-9]{64}$/.test(envelope.sha256) && digest(envelope.body) === envelope.sha256);
  const body = envelope.body;
  assert(exactKeys(body, ['schemaVersion', 'sourceRevision', 'runId', 'runAttempt', 'observationStart', 'generatedAt', 'cliVersion', 'checkpoints']) && body.schemaVersion === 1 && body.sourceRevision === run.head_sha && body.runId === String(run.id) && body.runAttempt === run.run_attempt && body.observationStart === observationStart && body.cliVersion === CLI_VERSION && validTime(body.generatedAt) && Date.parse(body.generatedAt) <= now + 60_000 && Array.isArray(body.checkpoints) && body.checkpoints.length <= MAX_HISTORY);
  const seen = new Set();
  for (const checkpoint of body.checkpoints) {
    assert(exactKeys(checkpoint, ['from', 'until', 'queriedFrom', 'collectedAt', 'queryCoverage', 'counts', 'reasons']) && [checkpoint.from, checkpoint.until, checkpoint.queriedFrom, checkpoint.collectedAt].every(validTime));
    const start = Date.parse(checkpoint.from), end = Date.parse(checkpoint.until);
    assert(start % QUARTER === 0 && end === start + QUARTER && !seen.has(start) && Date.parse(checkpoint.queriedFrom) === Math.max(start, Date.parse(observationStart)) && start >= Date.parse(observationStart) - QUARTER && end <= Date.parse(body.generatedAt) - 300_000 && Date.parse(checkpoint.collectedAt) >= end && Date.parse(checkpoint.collectedAt) <= Date.parse(body.generatedAt) && ['exhausted', 'partial'].includes(checkpoint.queryCoverage) && Array.isArray(checkpoint.reasons) && checkpoint.reasons.length <= REASONS.size && checkpoint.reasons.every(reason => REASONS.has(reason)) && new Set(checkpoint.reasons).size === checkpoint.reasons.length && (checkpoint.queryCoverage === 'exhausted') === (checkpoint.reasons.length === 0));
    parseCounts(checkpoint.counts);
    seen.add(start);
  }
  return body.checkpoints;
}

/** Narrow in-memory ZIP: exactly one regular checkpoint.json, no extraction paths. */
export function unzipCheckpoint(zip) {
  assert(Buffer.isBuffer(zip) && zip.length <= MAX_ARTIFACT && zip.length >= 22);
  let eocd = -1;
  for (let i = zip.length - 22; i >= Math.max(0, zip.length - 65557); i--) if (zip.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  assert(eocd >= 0 && zip.readUInt16LE(eocd + 4) === 0 && zip.readUInt16LE(eocd + 6) === 0 && zip.readUInt16LE(eocd + 8) === 1 && zip.readUInt16LE(eocd + 10) === 1);
  const central = zip.readUInt32LE(eocd + 16);
  assert(central + 46 <= eocd && zip.readUInt32LE(central) === 0x02014b50);
  const flags = zip.readUInt16LE(central + 8), method = zip.readUInt16LE(central + 10), compressed = zip.readUInt32LE(central + 20), uncompressed = zip.readUInt32LE(central + 24), nameLength = zip.readUInt16LE(central + 28), external = zip.readUInt32LE(central + 38), offset = zip.readUInt32LE(central + 42);
  assert(!(flags & 1) && [0, 8].includes(method) && uncompressed <= MAX_ARTIFACT && zip.subarray(central + 46, central + 46 + nameLength).toString() === 'checkpoint.json' && ((external >>> 16) & 0xf000) !== 0xa000 && offset + 30 <= central && zip.readUInt32LE(offset) === 0x04034b50);
  const localNameLength = zip.readUInt16LE(offset + 26), extra = zip.readUInt16LE(offset + 28);
  assert(zip.subarray(offset + 30, offset + 30 + localNameLength).toString() === 'checkpoint.json' && zip.readUInt16LE(offset + 8) === method);
  const begin = offset + 30 + localNameLength + extra;
  assert(begin + compressed <= central);
  const data = method === 0 ? zip.subarray(begin, begin + compressed) : inflateRawSync(zip.subarray(begin, begin + compressed), { maxOutputLength: MAX_ARTIFACT });
  assert(data.length === uncompressed);
  return JSON.parse(data.toString('utf8'));
}
async function githubApi(endpoint, binary = false) {
  const result = await capture('gh', ['api', endpoint], { env: { GH_TOKEN: process.env.GH_TOKEN }, maxBytes: binary ? MAX_ARTIFACT : 1024 * 1024 });
  assert(result.ok);
  return binary ? result.data : JSON.parse(result.data.toString('utf8'));
}
export async function loadHistory({ repository, observationStart, now = Date.now(), api = githubApi }) {
  let rejected = 0;
  try {
    const listing = await api(`repos/${repository}/actions/workflows/company-selection-observation.yml/runs?branch=main&status=completed&per_page=30`);
    assert(Array.isArray(listing.workflow_runs) && listing.workflow_runs.length <= 30);
    for (const run of listing.workflow_runs) {
      try {
        validateRun(run, now); // Reject metadata BEFORE downloading anything.
        const artifacts = await api(`repos/${repository}/actions/runs/${run.id}/artifacts?per_page=100`);
        assert(Array.isArray(artifacts.artifacts) && artifacts.artifacts.length <= 100);
        const artifact = artifacts.artifacts.find(value => value.name === `company-selection-checkpoints-${run.id}-${run.run_attempt}`);
        if (!artifact) continue;
        validateArtifact(artifact, run);
        const archive = await api(`repos/${repository}/actions/artifacts/${artifact.id}/zip`, true);
        // GitHub artifact digest is the SHA256 of the ZIP archive, not its JSON.
        assert(createHash('sha256').update(archive).digest('hex') === artifact.digest.slice(7));
        const checkpoints = validateSnapshot(unzipCheckpoint(archive), run, observationStart, now);
        return { checkpoints, status: rejected ? 'validated_after_rejected_candidate' : 'validated', rejected };
      } catch { rejected++; }
    }
    return { checkpoints: [], status: rejected ? 'unavailable_rejected_candidates' : 'not_found', rejected };
  } catch { return { checkpoints: [], status: 'unavailable', rejected }; }
}

export function replay(previous, next) {
  if (!previous) return next;
  const before = parseCounts(previous.counts), after = parseCounts(next.counts);
  if ([...before].some(([pair, count]) => count > (after.get(pair) ?? 0))) {
    return { ...next, counts: countRows(mergeCounts(before, after, true)), queryCoverage: 'partial', reasons: [...new Set([...next.reasons, 'replay_count_regression'])].sort() };
  }
  return next;
}
export function summarize(checkpoints, { from, until, observationStart, retention, now, fullDurationReady = true }) {
  const lookup = new Map(checkpoints.map(value => [Date.parse(value.from), value]));
  const start = Math.max(from, observationStart), counts = empty();
  let expected = 0, missing = 0, partial = 0, exhausted = 0, expired = 0;
  const reasons = new Set();
  for (let quarter = Math.floor(start / QUARTER) * QUARTER; quarter < until; quarter += QUARTER) {
    expected++;
    const value = lookup.get(quarter);
    if (!value) { missing++; if (!retention || Math.max(quarter, observationStart) < now - retention + 120_000) expired++; continue; }
    if (value.queryCoverage === 'exhausted') exhausted++; else partial++;
    value.reasons.forEach(reason => reasons.add(reason));
    const values = parseCounts(value.counts);
    for (const [pair, count] of values) counts.set(pair, (counts.get(pair) ?? 0) + count);
  }
  const positiveControl = checkpoints.some(value => value.counts.length > 0);
  const blockers = [];
  if (!fullDurationReady) blockers.push('duration_not_reached');
  if (missing) blockers.push('missing_windows');
  if (partial) blockers.push('partial_windows');
  if (!positiveControl) blockers.push('no_positive_telemetry_observed');
  return { from: iso(start), until: iso(Math.max(start, until)), counts: countRows(counts), completeness: {
    queryCoverage: missing || partial ? 'partial' : 'exhausted', expectedWindows: expected, exhaustedWindows: exhausted, missingWindows: missing, partialWindows: partial, expiredOrRetentionUnknownMissingWindows: expired, fullDurationReady,
    instrumentationCoverage: 'operator_declared_since_start', positiveTelemetryObserved: positiveControl,
    providerCaptureGuarantee: 'unknown', ingestionFinality: 'unknown_replayed_with_five_minute_lag', timestampBasis: 'request', countsAreLowerBound: Boolean(missing || partial || !positiveControl),
    zeroClaims: blockers.length ? 'blocked' : 'observed_only_under_conditional_provider_capture', rolloutAcceptanceBlockers: blockers, reasons: [...reasons].sort(),
  } };
}
export async function collect({ observationStart, previous = [], run, project, scope, plan, now = Date.now, maxQueries = 24, queryOverride }) {
  const checkedAt = now(), until = Math.floor((checkedAt - 300_000) / QUARTER) * QUARTER, activation = Date.parse(observationStart), retention = RETENTION[plan];
  assert(validTime(observationStart) && activation <= checkedAt);
  const oldest = Math.max(Math.floor(activation / QUARTER) * QUARTER, until - 7 * 86400_000);
  const byTime = new Map(previous.filter(value => Date.parse(value.from) >= oldest && Date.parse(value.until) <= until).map(value => [Date.parse(value.from), value]));
  if (retention) {
    const service = collector({ run, project, scope, retention, now, maxQueries, queryOverride });
    for (let quarter = until - QUARTER; quarter >= oldest && service.budget(); quarter -= QUARTER) {
      const start = Math.max(quarter, activation);
      if (start < now() - retention + 120_000) break;
      // Replay every retained window on Hobby. For larger plans replay the last
      // hour and fill missing history; repeat runs can finish bounded catch-up.
      if (quarter < until - 3600_000 && byTime.has(quarter)) continue;
      const result = await service.window(start, quarter + QUARTER);
      const next = { from: iso(quarter), until: iso(quarter + QUARTER), queriedFrom: iso(start), collectedAt: iso(now()), queryCoverage: result.reasons.size ? 'partial' : 'exhausted', counts: countRows(result.counts), reasons: [...result.reasons].sort() };
      byTime.set(quarter, replay(byTime.get(quarter), next));
    }
  }
  const checkpoints = [...byTime].sort(([a], [b]) => a - b).map(([, value]) => value);
  const periods = { '1h': 3600_000, '24h': 86400_000, '7d': 7 * 86400_000 };
  const reports = Object.fromEntries(Object.entries(periods).map(([name, duration]) => [name, summarize(checkpoints, { from: until - duration, until, observationStart: activation, retention, now: checkedAt, fullDurationReady: until - activation >= duration })]));
  reports.sinceRollout = summarize(checkpoints, { from: oldest, until, observationStart: activation, retention, now: checkedAt, fullDurationReady: until > activation });
  return { checkpoints, report: { checkedAt: iso(checkedAt), checkpointMinutes: 15, cliVersion: CLI_VERSION, plan, retentionHours: retention ? retention / 3600_000 : null, observabilityPlus: 'not_established', reports } };
}

async function main() {
  const observationStart = process.env.COMPANY_REFERENCE_OBSERVATION_START;
  if (!observationStart) { console.log(JSON.stringify({ observation: 'disabled', reason: 'start_variable_unset' })); return; }
  const local = process.argv.includes('--local-cli');
  let report;
  try {
    assert(validTime(observationStart) && Date.parse(observationStart) <= Date.now());
    if (!local) {
      assert(process.env.GITHUB_REF === 'refs/heads/main' && ['schedule', 'workflow_dispatch'].includes(process.env.GITHUB_EVENT_NAME));
      if (process.env.GITHUB_EVENT_NAME === 'workflow_dispatch') assert(process.env.GITHUB_ACTOR === OWNER && process.env.GITHUB_TRIGGERING_ACTOR === OWNER);
      assert(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(process.env.GITHUB_REPOSITORY));
    }
    const run = vercelRunner(local);
    const [versionCommand, versionArgs] = run(['--version']);
    const version = await capture(versionCommand, versionArgs, { maxBytes: 8192 });
    assert(version.ok && version.data.toString().trim() === CLI_VERSION);
    const project = process.env.VERCEL_PROJECT_ID, scope = process.env.VERCEL_ORG_ID;
    assert(typeof project === 'string' && /^prj_[A-Za-z0-9]+$/.test(project) && typeof scope === 'string' && /^(?:team_[A-Za-z0-9]+|[a-z0-9-]+)$/.test(scope));
    const [planCommand, planArgs] = run(['whoami', '--scope', scope, '--format', 'json']);
    const planResult = await capture(planCommand, planArgs, { env: { VERCEL_TOKEN: process.env.VERCEL_TOKEN }, maxBytes: 131072 });
    let plan = 'unknown';
    try { const candidate = JSON.parse(planResult.data.toString()).plan; if (planResult.ok && Object.hasOwn(RETENTION, candidate)) plan = candidate; } catch { /* identity remains private */ }
    let history = { checkpoints: [], status: 'local_no_history', rejected: 0 };
    if (!local) {
      assert(process.env.GITHUB_REF === 'refs/heads/main' && ['schedule', 'workflow_dispatch'].includes(process.env.GITHUB_EVENT_NAME));
      if (process.env.GITHUB_EVENT_NAME === 'workflow_dispatch') assert(process.env.GITHUB_ACTOR === OWNER && process.env.GITHUB_TRIGGERING_ACTOR === OWNER);
      assert(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(process.env.GITHUB_REPOSITORY));
      history = await loadHistory({ repository: process.env.GITHUB_REPOSITORY, observationStart });
    }
    const result = await collect({ observationStart, previous: history.checkpoints, run, project, scope, plan });
    report = { ...result.report, history: { status: history.status, rejectedCandidates: history.rejected } };
    if (!local) {
      assert(/^[a-f0-9]{40}$/.test(process.env.GITHUB_SHA) && /^\d+$/.test(process.env.GITHUB_RUN_ID) && /^[1-9]\d*$/.test(process.env.GITHUB_RUN_ATTEMPT));
      const body = { schemaVersion: 1, sourceRevision: process.env.GITHUB_SHA, runId: process.env.GITHUB_RUN_ID, runAttempt: Number(process.env.GITHUB_RUN_ATTEMPT), observationStart, generatedAt: iso(Date.now()), cliVersion: CLI_VERSION, checkpoints: result.checkpoints };
      const output = process.env.COMPANY_SELECTION_OBSERVATION_OUTPUT;
      assert(typeof output === 'string' && output.endsWith('/checkpoint.json'));
      await mkdir(dirname(output), { recursive: true, mode: 0o700 });
      await writeFile(output, JSON.stringify({ body, sha256: digest(body) }) + '\n', { mode: 0o600, flag: 'wx' });
      await writeFile(dirname(output) + '/summary.json', JSON.stringify(report) + '\n', { mode: 0o600, flag: 'wx' });
    }
    console.log(JSON.stringify(report));
    if (!RETENTION[plan] || report.reports.sinceRollout.completeness.missingWindows || report.reports.sinceRollout.completeness.partialWindows) process.exitCode = 2;
  } catch {
    // Never expose CLI stderr/stdout, raw payloads, signed artifact URLs, identity,
    // credentials, or exception messages, including on an unexpected failure.
    console.log(JSON.stringify({ observation: 'failed', completeness: 'unknown', reason: 'collection_failed_no_raw_diagnostics' }));
    process.exitCode = 2;
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
