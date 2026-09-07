/**
 * UI-free domain boundary for the first bounded AI-filter pilot.
 *
 * This module validates and serializes data that an authoritative repository
 * has already loaded. It does not authenticate a caller, authorize a run,
 * check entitlement or budget, select a provider, or grant permission to
 * execute. Those fail-closed controls belong to the execution boundary.
 */

export const AI_FILTER_CONTRACT_VERSION = 1 as const;
export const AI_FILTER_SEGMENT_LIMIT = 50;
export const AI_FILTER_QUERY_MAX_LENGTH = 1_000;
export const AI_FILTER_DECISION_RETENTION_DAYS = 30;

const RETENTION_MS =
  AI_FILTER_DECISION_RETENTION_DAYS * 24 * 60 * 60 * 1_000;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/;
const UNSAFE_QUERY_CONTROL_PATTERN =
  /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

export type AiFilterDecisionValue = "accepted" | "rejected";

export type AiFilterStopReason =
  | "entitlement_unavailable"
  | "watchlist_too_broad"
  | "budget_exhausted"
  | "kill_switch_active"
  | "cancelled"
  | "provider_unavailable"
  | "invalid_output"
  | "configuration_changed"
  | "policy_unavailable";

export interface AiFilterConfiguration {
  readonly version: typeof AI_FILTER_CONTRACT_VERSION;
  readonly configurationId: string;
  readonly ownerId: string;
  readonly watchlistId: string;
  readonly candidateConstraint: "canonical_structured_watchlist";
  readonly queryText: string;
  readonly queryRevision: number;
  readonly watchlistRevision: number;
}

export interface AiFilterCandidateSnapshot {
  readonly candidateId: string;
  readonly postingFirstSeenAt: string;
  readonly productExpiresAt: string;
}

export interface AiFilterSegmentRequest {
  readonly version: typeof AI_FILTER_CONTRACT_VERSION;
  readonly runId: string;
  readonly requestedAt: string;
  readonly configuration: AiFilterConfiguration;
  readonly candidates: readonly AiFilterCandidateSnapshot[];
}

export interface AiFilterClassifierDecision {
  readonly candidateId: string;
  readonly decision: AiFilterDecisionValue;
}

export interface AiFilterCompletedResult {
  readonly runId: string;
  readonly status: "completed";
  readonly decisions: readonly AiFilterClassifierDecision[];
}

export interface AiFilterStoppedResult {
  readonly runId: string;
  readonly status: "stopped";
  readonly stopReason: AiFilterStopReason;
  readonly decisions: readonly AiFilterClassifierDecision[];
}

export type AiFilterTerminalResult =
  | AiFilterCompletedResult
  | AiFilterStoppedResult;

export interface AiFilterProductDecision {
  readonly version: typeof AI_FILTER_CONTRACT_VERSION;
  readonly runId: string;
  readonly configurationId: string;
  readonly ownerId: string;
  readonly watchlistId: string;
  readonly queryRevision: number;
  readonly watchlistRevision: number;
  readonly candidateId: string;
  readonly decision: AiFilterDecisionValue;
  readonly postingFirstSeenAt: string;
  readonly decidedAt: string;
  readonly expiresAt: string;
}

export class AiFilterContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AiFilterContractError";
  }
}

function fail(message: string): never {
  throw new AiFilterContractError(message);
}

function requireRecord(value: unknown, field: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    fail(`${field} must be an object`);
  }

  return value as Record<string, unknown>;
}

function requireExactKeys(
  record: Record<string, unknown>,
  expected: readonly string[],
  field: string,
): void {
  const actual = Object.keys(record).sort();
  const wanted = [...expected].sort();
  if (
    actual.length !== wanted.length ||
    actual.some((key, index) => key !== wanted[index])
  ) {
    fail(`${field} contains missing or unsupported fields`);
  }
}

function requireLiteral<T extends string | number>(
  value: unknown,
  expected: T,
  field: string,
): T {
  if (value !== expected) fail(`${field} is unsupported`);
  return expected;
}

function requireRevision(value: unknown, field: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    fail(`${field} must be a positive safe integer`);
  }
  return value as number;
}

function requireOpaqueId(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 255 ||
    value.trim() !== value ||
    CONTROL_CHARACTER_PATTERN.test(value)
  ) {
    fail(`${field} must be a non-empty opaque identifier`);
  }
  return value;
}

function requireUuid(value: unknown, field: string): string {
  if (typeof value !== "string" || !UUID_PATTERN.test(value)) {
    fail(`${field} must be a canonical lowercase UUID`);
  }
  return value;
}

function requireCanonicalInstant(value: unknown, field: string): string {
  if (typeof value !== "string") fail(`${field} must be an ISO instant`);
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString() !== value) {
    fail(`${field} must be a canonical ISO instant`);
  }
  return value;
}

function addRetention(firstSeenAt: string): string {
  const expiresAt = new Date(firstSeenAt).getTime() + RETENTION_MS;
  if (!Number.isFinite(expiresAt)) fail("retention instant is out of range");

  const date = new Date(expiresAt);
  if (!Number.isFinite(date.getTime())) fail("retention instant is out of range");
  return date.toISOString();
}

function requireDenseArray(value: unknown, field: string): unknown[] {
  if (!Array.isArray(value)) fail(`${field} must be an array`);
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.prototype.hasOwnProperty.call(value, index)) {
      fail(`${field} must not contain sparse entries`);
    }
  }
  return value;
}

function parseConfiguration(value: unknown): AiFilterConfiguration {
  const record = requireRecord(value, "configuration");
  requireExactKeys(
    record,
    [
      "version",
      "configurationId",
      "ownerId",
      "watchlistId",
      "candidateConstraint",
      "queryText",
      "queryRevision",
      "watchlistRevision",
    ],
    "configuration",
  );

  const queryText = record.queryText;
  if (
    typeof queryText !== "string" ||
    queryText.trim() !== queryText ||
    queryText.length === 0 ||
    queryText.length > AI_FILTER_QUERY_MAX_LENGTH ||
    UNSAFE_QUERY_CONTROL_PATTERN.test(queryText)
  ) {
    fail("configuration.queryText is invalid");
  }

  return Object.freeze({
    version: requireLiteral(
      record.version,
      AI_FILTER_CONTRACT_VERSION,
      "configuration.version",
    ),
    configurationId: requireUuid(
      record.configurationId,
      "configuration.configurationId",
    ),
    ownerId: requireOpaqueId(record.ownerId, "configuration.ownerId"),
    watchlistId: requireUuid(record.watchlistId, "configuration.watchlistId"),
    candidateConstraint: requireLiteral(
      record.candidateConstraint,
      "canonical_structured_watchlist",
      "configuration.candidateConstraint",
    ),
    queryText,
    queryRevision: requireRevision(
      record.queryRevision,
      "configuration.queryRevision",
    ),
    watchlistRevision: requireRevision(
      record.watchlistRevision,
      "configuration.watchlistRevision",
    ),
  });
}

function parseCandidate(value: unknown): AiFilterCandidateSnapshot {
  const record = requireRecord(value, "candidate");
  requireExactKeys(
    record,
    ["candidateId", "postingFirstSeenAt", "productExpiresAt"],
    "candidate",
  );

  const postingFirstSeenAt = requireCanonicalInstant(
    record.postingFirstSeenAt,
    "candidate.postingFirstSeenAt",
  );
  const productExpiresAt = requireCanonicalInstant(
    record.productExpiresAt,
    "candidate.productExpiresAt",
  );
  if (productExpiresAt !== addRetention(postingFirstSeenAt)) {
    fail("candidate.productExpiresAt must equal the product retention boundary");
  }

  return Object.freeze({
    candidateId: requireUuid(record.candidateId, "candidate.candidateId"),
    postingFirstSeenAt,
    productExpiresAt,
  });
}

/**
 * Parses an immutable candidate-selection snapshot. It intentionally does not
 * bind normalized posting content; AF-9/AF-10 must bind classifier input at
 * execution time. Successful parsing is not authorization or an execution
 * permit.
 */
export function parseAiFilterSegmentRequest(
  input: unknown,
): AiFilterSegmentRequest {
  const record = requireRecord(input, "segment request");
  requireExactKeys(
    record,
    ["version", "runId", "requestedAt", "configuration", "candidates"],
    "segment request",
  );

  const requestedAt = requireCanonicalInstant(
    record.requestedAt,
    "segment request.requestedAt",
  );
  const requestedAtMs = new Date(requestedAt).getTime();
  const candidateInputs = requireDenseArray(
    record.candidates,
    "segment request.candidates",
  );
  if (
    candidateInputs.length === 0 ||
    candidateInputs.length > AI_FILTER_SEGMENT_LIMIT
  ) {
    fail(`segment request must contain 1-${AI_FILTER_SEGMENT_LIMIT} candidates`);
  }

  const seen = new Set<string>();
  let previousFirstSeenMs = Number.POSITIVE_INFINITY;
  const candidates = candidateInputs.map((candidateInput) => {
    const candidate = parseCandidate(candidateInput);
    if (seen.has(candidate.candidateId)) {
      fail("segment request contains duplicate candidates");
    }
    seen.add(candidate.candidateId);

    const firstSeenMs = new Date(candidate.postingFirstSeenAt).getTime();
    if (firstSeenMs > requestedAtMs) {
      fail("candidate cannot be seen after the run was requested");
    }
    if (firstSeenMs > previousFirstSeenMs) {
      fail("segment request candidates must be newest-first");
    }
    if (new Date(candidate.productExpiresAt).getTime() <= requestedAtMs) {
      fail("candidate retention expired before the run was requested");
    }
    previousFirstSeenMs = firstSeenMs;
    return candidate;
  });

  return Object.freeze({
    version: requireLiteral(
      record.version,
      AI_FILTER_CONTRACT_VERSION,
      "segment request.version",
    ),
    runId: requireUuid(record.runId, "segment request.runId"),
    requestedAt,
    configuration: parseConfiguration(record.configuration),
    candidates: Object.freeze(candidates),
  });
}

/**
 * Enforces opaque run-ID selection idempotency. A retry may reuse a run ID only
 * with the same configuration revision and candidate membership/order. This
 * does not bind normalized classifier content; the execution layer must do so.
 * No semantic data is placed into or returned as an idempotency key.
 */
export function assertSameAiFilterSelectionBinding(
  existingInput: unknown,
  retryInput: unknown,
): AiFilterSegmentRequest {
  const existing = parseAiFilterSegmentRequest(existingInput);
  const retry = parseAiFilterSegmentRequest(retryInput);
  if (existing.runId !== retry.runId) fail("run IDs do not match");

  const sameConfiguration =
    existing.configuration.version === retry.configuration.version &&
    existing.configuration.configurationId ===
      retry.configuration.configurationId &&
    existing.configuration.ownerId === retry.configuration.ownerId &&
    existing.configuration.watchlistId === retry.configuration.watchlistId &&
    existing.configuration.candidateConstraint ===
      retry.configuration.candidateConstraint &&
    existing.configuration.queryText === retry.configuration.queryText &&
    existing.configuration.queryRevision ===
      retry.configuration.queryRevision &&
    existing.configuration.watchlistRevision ===
      retry.configuration.watchlistRevision;
  const sameCandidates =
    existing.candidates.length === retry.candidates.length &&
    existing.candidates.every((candidate, index) => {
      const other = retry.candidates[index];
      return (
        other !== undefined &&
        candidate.candidateId === other.candidateId &&
        candidate.postingFirstSeenAt === other.postingFirstSeenAt &&
        candidate.productExpiresAt === other.productExpiresAt
      );
    });

  if (
    existing.version !== retry.version ||
    existing.requestedAt !== retry.requestedAt ||
    !sameConfiguration ||
    !sameCandidates
  ) {
    fail("run ID is already bound to a different selection snapshot");
  }
  return retry;
}

function parseDecision(value: unknown): AiFilterClassifierDecision {
  const record = requireRecord(value, "decision");
  requireExactKeys(record, ["candidateId", "decision"], "decision");
  if (record.decision !== "accepted" && record.decision !== "rejected") {
    fail("decision must be binary");
  }
  return Object.freeze({
    candidateId: requireUuid(record.candidateId, "decision.candidateId"),
    decision: record.decision,
  });
}

const STOP_REASONS = new Set<AiFilterStopReason>([
  "entitlement_unavailable",
  "watchlist_too_broad",
  "budget_exhausted",
  "kill_switch_active",
  "cancelled",
  "provider_unavailable",
  "invalid_output",
  "configuration_changed",
  "policy_unavailable",
]);

/** Validates and canonicalizes strict output against a selection snapshot. */
export function parseAiFilterTerminalResult(
  input: unknown,
  requestInput: unknown,
): AiFilterTerminalResult {
  const request = parseAiFilterSegmentRequest(requestInput);
  const record = requireRecord(input, "terminal result");
  if (record.status === "completed") {
    requireExactKeys(record, ["runId", "status", "decisions"], "terminal result");
  } else if (record.status === "stopped") {
    requireExactKeys(
      record,
      ["runId", "status", "stopReason", "decisions"],
      "terminal result",
    );
    if (!STOP_REASONS.has(record.stopReason as AiFilterStopReason)) {
      fail("terminal result stop reason is unsupported");
    }
  } else {
    fail("terminal result status is unsupported");
  }

  const runId = requireUuid(record.runId, "terminal result.runId");
  if (runId !== request.runId) fail("terminal result belongs to another run");

  const decisionInputs = requireDenseArray(
    record.decisions,
    "terminal result.decisions",
  );
  if (decisionInputs.length > request.candidates.length) {
    fail("terminal result contains too many decisions");
  }

  const candidateIds = new Set(
    request.candidates.map((candidate) => candidate.candidateId),
  );
  const decisionsByCandidate = new Map<string, AiFilterClassifierDecision>();
  for (const decisionInput of decisionInputs) {
    const decision = parseDecision(decisionInput);
    if (!candidateIds.has(decision.candidateId)) {
      fail("terminal result contains a foreign candidate");
    }
    if (decisionsByCandidate.has(decision.candidateId)) {
      fail("terminal result contains duplicate decisions");
    }
    decisionsByCandidate.set(decision.candidateId, decision);
  }
  const decisions = Object.freeze(
    request.candidates.flatMap((candidate) => {
      const decision = decisionsByCandidate.get(candidate.candidateId);
      return decision ? [decision] : [];
    }),
  );

  if (record.status === "completed") {
    if (decisions.length !== request.candidates.length) {
      fail("completed result must decide every candidate");
    }
    return Object.freeze({ runId, status: "completed", decisions });
  }

  if (decisions.length === request.candidates.length) {
    fail("stopped result must leave at least one candidate undecided");
  }

  return Object.freeze({
    runId,
    status: "stopped",
    stopReason: record.stopReason as AiFilterStopReason,
    decisions,
  });
}

/** Attaches owner/configuration and product-retention provenance for storage. */
export function materializeAiFilterProductDecisions(
  requestInput: unknown,
  resultInput: unknown,
  decidedAtInput: unknown,
): readonly AiFilterProductDecision[] {
  const request = parseAiFilterSegmentRequest(requestInput);
  const result = parseAiFilterTerminalResult(resultInput, request);
  const decidedAt = requireCanonicalInstant(decidedAtInput, "decidedAt");
  if (new Date(decidedAt).getTime() < new Date(request.requestedAt).getTime()) {
    fail("decidedAt cannot precede requestedAt");
  }

  const candidates = new Map(
    request.candidates.map((candidate) => [candidate.candidateId, candidate]),
  );
  return Object.freeze(result.decisions.map((decision) => {
    const candidate = candidates.get(decision.candidateId);
    if (!candidate) fail("decision candidate is missing from the snapshot");
    return Object.freeze({
      version: AI_FILTER_CONTRACT_VERSION,
      runId: request.runId,
      configurationId: request.configuration.configurationId,
      ownerId: request.configuration.ownerId,
      watchlistId: request.configuration.watchlistId,
      queryRevision: request.configuration.queryRevision,
      watchlistRevision: request.configuration.watchlistRevision,
      candidateId: decision.candidateId,
      decision: decision.decision,
      postingFirstSeenAt: candidate.postingFirstSeenAt,
      decidedAt,
      expiresAt: candidate.productExpiresAt,
    });
  }));
}

export function isAiFilterProductDecisionExpired(
  decision: Pick<AiFilterProductDecision, "expiresAt">,
  nowInput: unknown,
): boolean {
  const expiresAt = requireCanonicalInstant(decision.expiresAt, "expiresAt");
  const now = requireCanonicalInstant(nowInput, "now");
  return new Date(now).getTime() >= new Date(expiresAt).getTime();
}
