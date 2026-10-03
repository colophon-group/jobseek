import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { deflateRawSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { CLI_VERSION, QUARTER, capture, countRows, parseCounts, parseRequest, collector, logsArgs, validateRun, validateArtifact, validateSnapshot, unzipCheckpoint, loadHistory, replay, summarize, collect, digest } from './collect-company-selection-telemetry.mjs';
const EVENT = 'company_selection_mutation';
const NOW = Date.parse('2026-10-03T12:40:00.000Z');
const START = '2026-10-03T12:00:00.000Z';
const iso = value => new Date(value).toISOString();
const event = (operation = 'create', outcome = 'success') => JSON.stringify({ event: EVENT, operation, outcome });
const result = () => ({ rows: 0, counts: new Map(), reasons: new Set() });
const row = (messages, timestamp = NOW - 20 * 60_000, id = 'ephemeral-customer-request-id') => JSON.stringify({ id, timestamp, message: event(), requestPath: '/private/customer/path', domain: 'private-domain', logs: messages.map(message => ({ message })) });
const run = () => ({ id: 1234, status: 'completed', head_branch: 'main', path: '.github/workflows/company-selection-observation.yml', event: 'workflow_dispatch', head_sha: 'a'.repeat(40), run_attempt: 1, updated_at: iso(NOW), actor: { login: 'viktor-shcherb' }, triggering_actor: { login: 'viktor-shcherb' } });
const checkpoint = (start = Date.parse(START), count = 1) => ({ from: iso(start), until: iso(start + QUARTER), queriedFrom: iso(start), collectedAt: iso(NOW), queryCoverage: 'exhausted', counts: count ? [{ operation: 'star', outcome: 'success', count }] : [], reasons: [] });
const envelope = () => {
  const r = run();
  const body = { schemaVersion: 1, sourceRevision: r.head_sha, runId: String(r.id), runAttempt: r.run_attempt, observationStart: START, generatedAt: iso(NOW), cliVersion: CLI_VERSION, checkpoints: [checkpoint()] };
  return { body, sha256: digest(body) };
};
function zip(value, { name = 'checkpoint.json', count = 1, symlink = false, method = 8 } = {}) {
  const nameBuffer = Buffer.from(name), payload = Buffer.from(JSON.stringify(value)), compressed = method === 8 ? deflateRawSync(payload) : payload;
  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50); local.writeUInt16LE(method, 8); local.writeUInt32LE(compressed.length, 18); local.writeUInt32LE(payload.length, 22); local.writeUInt16LE(nameBuffer.length, 26);
  const header = Buffer.alloc(46);
  header.writeUInt32LE(0x02014b50); header.writeUInt16LE(method, 10); header.writeUInt32LE(compressed.length, 20); header.writeUInt32LE(payload.length, 24); header.writeUInt16LE(nameBuffer.length, 28);
  if (symlink) header.writeUInt32LE((0xa000 << 16) >>> 0, 38);
  const central = Buffer.concat([header, nameBuffer]);
  const ending = Buffer.alloc(22);
  ending.writeUInt32LE(0x06054b50); ending.writeUInt16LE(count, 8); ending.writeUInt16LE(count, 10); ending.writeUInt32LE(central.length, 12); ending.writeUInt32LE(local.length + nameBuffer.length + compressed.length, 16);
  return Buffer.concat([local, nameBuffer, compressed, central, ending]);
}
test('counts every child event and excludes top-level display duplicate', () => {
  const value = result();
  parseRequest(row([event(), event('star', 'lookup_miss')]), NOW - 3600_000, NOW, new Set(), value);
  assert.equal([...value.counts.values()].reduce((a, b) => a + b), 2);
  assert.deepEqual([...value.reasons], []);
  assert.equal(countRows(value.counts).length, 2);
});
test('privacy: unrelated fields and raw messages cannot reach serialized counts or reasons', () => {
  const value = result();
  parseRequest(row([event(), 'customer@example.test password secret private']), NOW - 3600_000, NOW, new Set(), value);
  const output = JSON.stringify({ counts: countRows(value.counts), reasons: [...value.reasons] });
  for (const forbidden of ['customer', 'password', 'secret', 'private', 'ephemeral']) assert.ok(!output.includes(forbidden));
});
for (const [name, input, reason] of [
  ['malformed raw row', 'private-customer-secret', 'malformed_request_record'],
  ['missing logs', JSON.stringify({ id: 'x', timestamp: NOW, message: event() }), 'unsupported_log_schema'],
  ['invalid timestamp', JSON.stringify({ id: 'x', timestamp: 'secret', logs: [] }), 'invalid_request_timestamp'],
  ['unknown outcome', row([event('star', 'private-outcome')]), 'unrecognized_telemetry_contract'],
  ['candidate text', row([`prefix ${event()}`]), 'unparsed_candidate_message'],
]) test(`marks ${name} incomplete without revealing it`, () => {
  const value = result();
  parseRequest(input, NOW - 3600_000, NOW + 1, new Set(), value);
  assert.ok(value.reasons.has(reason)); assert.equal(value.counts.size, 0);
});
test('truncation and provider limits remain explicit even when an event parses', () => {
  const value = result(), parsed = JSON.parse(row([event(), ...Array(255).fill('unrelated')]));
  parsed.logs[0].messageTruncated = true;
  parseRequest(JSON.stringify(parsed), NOW - 3600_000, NOW, new Set(), value);
  assert.ok(value.reasons.has('possible_provider_request_line_cap'));
  assert.ok(value.reasons.has('truncated_request_message'));
});
test('half-open timestamps and transient request deduplication avoid double counts', () => {
  const value = result(), seen = new Set(), start = NOW - 3600_000;
  parseRequest(row([event()], NOW), start, NOW, seen, value);
  parseRequest(row([event()]), start, NOW, seen, value);
  parseRequest(row([event()]), start, NOW, seen, value);
  assert.equal(value.counts.get('create:success'), 1);
  assert.ok(value.reasons.has('duplicate_request_rows'));
});
test('production log command has fixed absolute bounds and no deployment/branch restriction', () => {
  const args = logsArgs({ project: 'prj_fixture', scope: 'team_fixture', start: NOW - QUARTER, end: NOW, limit: 1000 });
  assert.equal(args[args.indexOf('--since') + 1], iso(NOW - QUARTER));
  assert.equal(args[args.indexOf('--environment') + 1], 'production');
  assert.ok(!args.includes('--branch') && !args.includes('--deployment') && !args.includes('--follow'));
});
test('saturated parent is discarded and all child pages/windows counted', async () => {
  let calls = 0;
  const service = collector({ retention: 3600_000, now: () => NOW, deadline: NOW + 1000, limit: 2, queryOverride: async (start, end) => {
    calls++; const value = result(); value.rows = end - start > 1000 ? 2 : 1; value.counts.set('create:success', value.rows); return value;
  } });
  const value = await service.window(NOW - 2000, NOW);
  assert.equal(calls, 3); assert.equal(value.counts.get('create:success'), 2); assert.equal(value.reasons.size, 0);
});
test('saturated minimum and exhausted budgets never claim zero/complete', async () => {
  const service = collector({ retention: 3600_000, now: () => NOW, deadline: NOW + 1000, limit: 2, maxQueries: 1, queryOverride: async () => { const value = result(); value.rows = 2; return value; } });
  assert.ok((await service.window(NOW - 2000, NOW)).reasons.has('saturated_query_budget'));
});
test('retention checked at each query boundary prevents a false historical zero', async () => {
  const service = collector({ retention: 3600_000, now: () => NOW, deadline: NOW + 1000, queryOverride: async () => { throw new Error('must not query'); } });
  assert.ok((await service.window(NOW - 3600_000, NOW - QUARTER)).reasons.has('retention_boundary'));
});
test('actual subprocess streaming ignores private stderr and bounds raw output', async () => {
  const value = result();
  const response = await capture(process.execPath, ['-e', `console.error('private-secret');console.log(${JSON.stringify(row([event()]))});`], { onLine: line => parseRequest(line, NOW - 3600_000, NOW, new Set(), value) });
  assert.equal(response.ok, true); assert.equal(response.data.length, 0); assert.equal(value.counts.get('create:success'), 1);
  const oversized = await capture(process.execPath, ['-e', 'process.stdout.write("x".repeat(10000))'], { maxBytes: 100 });
  assert.equal(oversized.ok, false); assert.equal(oversized.reason, 'query_transport_budget_exhausted'); assert.equal(oversized.data.length, 0);
});
test('bounded counts reject extra fields, duplicates and unknown cardinality', () => {
  assert.throws(() => parseCounts([{ operation: 'star', outcome: 'success', count: 1, requestId: 'secret' }]));
  assert.throws(() => parseCounts([{ operation: 'private', outcome: 'success', count: 1 }]));
  assert.throws(() => parseCounts([{ operation: 'star', outcome: 'success', count: 1 }, { operation: 'star', outcome: 'success', count: 2 }]));
});
for (const [name, mutate] of Object.entries({
  actor: r => r.actor.login = 'other', triggeringActor: r => r.triggering_actor.login = 'other', branch: r => r.head_branch = 'feature', path: r => r.path = 'other.yml', event: r => r.event = 'pull_request', future: r => r.updated_at = iso(NOW + 120_000), stale: r => r.updated_at = iso(NOW - 9 * 86400_000),
})) test(`rejects untrusted run ${name} before download`, async () => {
  const candidate = run(); mutate(candidate); let requested = 0;
  const history = await loadHistory({ repository: 'fixture/jobseek', observationStart: START, now: NOW, api: async endpoint => { if (endpoint.includes('/workflows/')) return { workflow_runs: [candidate] }; requested++; throw new Error('private'); } });
  assert.equal(requested, 0); assert.equal(history.checkpoints.length, 0); assert.equal(history.rejected, 1);
});
test('scheduled main run supports durable readonly history', () => { const r = run(); r.event = 'schedule'; validateRun(r, NOW); });
test('exact one-file ZIP accepts deflate and store; no extraction touches disk', () => {
  for (const method of [0, 8]) assert.deepEqual(unzipCheckpoint(zip(envelope(), { method })), envelope());
  for (const options of [{ name: '../checkpoint.json' }, { name: '/checkpoint.json' }, { count: 2 }, { symlink: true }, { method: 9 }]) assert.throws(() => unzipCheckpoint(zip(envelope(), options)));
});
test('archive digest/source/run/attempt bound validated aggregate history loads', async () => {
  const r = run(), bytes = zip(envelope());
  const artifact = { id: 7, name: 'company-selection-checkpoints-1234-1', expired: false, size_in_bytes: bytes.length, digest: 'sha256:' + createHash('sha256').update(bytes).digest('hex') };
  validateArtifact(artifact, r);
  const history = await loadHistory({ repository: 'fixture/jobseek', observationStart: START, now: NOW, api: async endpoint => endpoint.includes('/workflows/') ? { workflow_runs: [r] } : endpoint.includes('/runs/') ? { artifacts: [artifact] } : bytes });
  assert.equal(history.status, 'validated'); assert.equal(history.checkpoints.length, 1);
});
for (const [name, mutate] of Object.entries({
  privateField: b => b.customerId = 'private', privateReason: b => { b.checkpoints[0].reasons = ['private-secret']; b.checkpoints[0].queryCoverage = 'partial'; }, source: b => b.sourceRevision = 'b'.repeat(40), attempt: b => b.runAttempt = 2, warm: b => b.checkpoints[0].until = iso(NOW), unknown: b => b.checkpoints[0].counts[0].outcome = 'private', futureCollection: b => b.checkpoints[0].collectedAt = iso(NOW + 1),
})) test(`rejects checkpoint ${name} even with recomputed content digest`, () => {
  const e = envelope(); mutate(e.body); e.sha256 = digest(e.body); assert.throws(() => validateSnapshot(e, run(), START, NOW));
});
test('snapshot validates complete fixed schema and content digest', () => {
  assert.equal(validateSnapshot(envelope(), run(), START, NOW).length, 1);
  const e = envelope(); e.sha256 = 'b'.repeat(64); assert.throws(() => validateSnapshot(e, run(), START, NOW));
});
test('replay replaces counts; regression preserves bounded observations with explicit gap', () => {
  const before = checkpoint(), next = checkpoint(Date.parse(START), 2);
  assert.equal(replay(before, next).counts[0].count, 2);
  const regressed = replay(next, before);
  assert.equal(regressed.counts[0].count, 2); assert.equal(regressed.queryCoverage, 'partial'); assert.ok(regressed.reasons.includes('replay_count_regression'));
});
test('24h/7day summaries cannot disguise missing/expired windows as zero', () => {
  for (const hours of [24, 168]) {
    const end = Math.floor(NOW / QUARTER) * QUARTER;
    const report = summarize([], { from: end - hours * 3600_000, until: end, observationStart: NOW - 8 * 86400_000, retention: 3600_000, now: NOW });
    assert.equal(report.counts.length, 0); assert.equal(report.completeness.missingWindows, hours * 4); assert.equal(report.completeness.zeroClaims, 'blocked'); assert.ok(report.completeness.expiredOrRetentionUnknownMissingWindows > 0);
  }
});
test('empty exhausted windows still require positive deployed instrumentation evidence', () => {
  const report = summarize([checkpoint(Date.parse(START), 0)], { from: Date.parse(START), until: Date.parse(START) + QUARTER, observationStart: Date.parse(START), retention: 3600_000, now: NOW });
  assert.equal(report.completeness.queryCoverage, 'exhausted'); assert.equal(report.completeness.zeroClaims, 'blocked'); assert.ok(report.completeness.rolloutAcceptanceBlockers.includes('no_positive_telemetry_observed'));
});
test('quarter-hour collector preserves historical checkpoint and replays retained windows', async () => {
  const activation = iso(NOW - 26 * 3600_000), old = checkpoint(Math.floor((NOW - 24 * 3600_000) / QUARTER) * QUARTER);
  let calls = 0;
  const output = await collect({ observationStart: activation, previous: [old], plan: 'hobby', now: () => NOW, queryOverride: async () => { calls++; return result(); } });
  assert.equal(calls, 3); assert.ok(output.checkpoints.some(value => value.from === old.from)); assert.equal(output.report.reports['24h'].completeness.zeroClaims, 'blocked');
});
test('unknown retention never runs a historical query', async () => {
  const output = await collect({ observationStart: START, plan: 'unknown', now: () => NOW, queryOverride: async () => { throw new Error('must not query'); } });
  assert.equal(output.checkpoints.length, 0); assert.ok(output.report.reports.sinceRollout.completeness.missingWindows > 0);
});
test('warm-up before first closed window never reports an inverted time interval', async () => {
  const output = await collect({ observationStart: iso(NOW - 60_000), plan: 'hobby', now: () => NOW, queryOverride: async () => { throw new Error('must not query'); } });
  const report = output.report.reports.sinceRollout;
  assert.ok(Date.parse(report.from) <= Date.parse(report.until)); assert.equal(report.completeness.fullDurationReady, false);
});
test('unset activation is a real CLI no-op and touches no external services', () => {
  const child = spawnSync(process.execPath, ['scripts/collect-company-selection-telemetry.mjs'], { encoding: 'utf8', env: { PATH: '/nonexistent' } });
  assert.equal(child.status, 0); assert.deepEqual(JSON.parse(child.stdout), { observation: 'disabled', reason: 'start_variable_unset' });
});
test('workflow is inert without activation; readonly main-only scheduled and owner manual', () => {
  const workflow = readFileSync('.github/workflows/company-selection-observation.yml', 'utf8');
  assert.match(workflow, /cron: "3,18,33,48 \* \* \* \*"/);
  assert.match(workflow, /github\.ref == 'refs\/heads\/main'/);
  assert.match(workflow, /github\.actor == 'viktor-shcherb'/);
  assert.match(workflow, /github\.triggering_actor == 'viktor-shcherb'/);
  assert.match(workflow, /vars\.COMPANY_REFERENCE_OBSERVATION_START != ''/);
  assert.match(workflow, /environment: Production/);
  assert.match(workflow, /actions: read/);
  assert.match(workflow, /contents: read/);
  assert.ok(!/write\b|secrets\.DATABASE|RESTIC|--token|drain|vercel@59/.test(workflow));
  assert.match(workflow, /pnpm dlx vercel@62\.1\.0 --version/);
  assert.match(workflow, /retention-days: 14/);
  assert.match(workflow, /path: \$\{\{ runner\.temp \}\}\/company-selection-observation\/checkpoint\.json/);
  const uses = [...workflow.matchAll(/uses: ([^\s#]+)/g)].map(value => value[1]);
  assert.deepEqual(uses, ['actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1', 'pnpm/action-setup@ea17c68df8912ef543352723c149a84f56e3d413', 'actions/setup-node@820762786026740c76f36085b0efc47a31fe5020', 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a']);
});
