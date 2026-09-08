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
export const AI_FILTER_SOFT_QUERY_NORMALIZER_VERSION = 1 as const;
export const AI_FILTER_DECISION_RETENTION_DAYS = 30;

const AI_FILTER_QUERY_RAW_MAX_CODE_UNITS = AI_FILTER_QUERY_MAX_LENGTH * 4;

const RETENTION_MS =
  AI_FILTER_DECISION_RETENTION_DAYS * 24 * 60 * 60 * 1_000;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/;
const QUERY_CONTROL_PATTERN = /\p{Cc}/u;
const QUERY_DEFAULT_IGNORABLE_PATTERN =
  /\p{Default_Ignorable_Code_Point}/u;
const QUERY_WHITESPACE_PATTERN = /\p{White_Space}/u;
const QUERY_WHITESPACE_RUN_PATTERN = /\p{White_Space}+/gu;

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

function inspectArray(value: unknown, field: string): value is unknown[] {
  try {
    return Array.isArray(value);
  } catch {
    fail(`${field} could not be inspected`);
  }
}

function inspectOwnKeys(value: object, field: string): readonly PropertyKey[] {
  try {
    return Reflect.ownKeys(value);
  } catch {
    fail(`${field} could not be inspected`);
  }
}

function inspectOwnPropertyDescriptor(
  value: object,
  key: PropertyKey,
  field: string,
): PropertyDescriptor | undefined {
  try {
    return Object.getOwnPropertyDescriptor(value, key);
  } catch {
    fail(`${field} could not be inspected`);
  }
}

function snapshotDataRecord(
  value: unknown,
  field: string,
): Record<string, unknown> {
  if (value === null || typeof value !== "object" || inspectArray(value, field)) {
    fail(`${field} must be an object`);
  }

  const record = value as Record<string, unknown>;
  const snapshot = Object.create(null) as Record<string, unknown>;
  for (const key of inspectOwnKeys(record, field)) {
    if (typeof key !== "string") fail(`${field} must contain plain data fields`);
    const descriptor = inspectOwnPropertyDescriptor(record, key, field);
    if (!descriptor || !("value" in descriptor) || !descriptor.enumerable) {
      fail(`${field} must contain plain data fields`);
    }
    snapshot[key] = descriptor.value;
  }
  return snapshot;
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

/**
 * Parses the single canonical candidate-ID format shared by selection and
 * classifier boundaries.
 */
export function parseAiFilterCandidateId(input: unknown): string {
  return requireUuid(input, "AI filter candidate ID");
}

/**
 * Version 1 of the authoritative soft-query boundary. Length is measured in
 * Unicode code points after NFC and whitespace canonicalization, not UTF-16
 * code units.
 */
export function normalizeAiFilterSoftQueryV1(input: unknown): string {
  if (typeof input !== "string") {
    fail("AI filter soft query must be text");
  }
  if (input.length > AI_FILTER_QUERY_RAW_MAX_CODE_UNITS) {
    fail("AI filter soft query raw input is too large");
  }

  for (const codePoint of input) {
    if (
      QUERY_DEFAULT_IGNORABLE_PATTERN.test(codePoint) ||
      (QUERY_CONTROL_PATTERN.test(codePoint) &&
        !QUERY_WHITESPACE_PATTERN.test(codePoint))
    ) {
      fail("AI filter soft query contains unsupported code points");
    }
  }

  const normalized = input
    .normalize("NFC")
    .replace(QUERY_WHITESPACE_RUN_PATTERN, " ")
    .trim();
  if (normalized.length === 0) {
    fail("AI filter soft query must not be empty");
  }

  let codePointCount = 0;
  for (const _codePoint of normalized) {
    codePointCount += 1;
    if (codePointCount > AI_FILTER_QUERY_MAX_LENGTH) {
      fail(
        `AI filter soft query must not exceed ${AI_FILTER_QUERY_MAX_LENGTH} Unicode code points`,
      );
    }
  }
  return normalized;
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
  if (!inspectArray(value, field)) fail(`${field} must be an array`);
  const lengthDescriptor = inspectOwnPropertyDescriptor(value, "length", field);
  if (
    !lengthDescriptor ||
    !("value" in lengthDescriptor) ||
    !Number.isSafeInteger(lengthDescriptor.value) ||
    lengthDescriptor.value < 0 ||
    lengthDescriptor.value > AI_FILTER_SEGMENT_LIMIT
  ) {
    fail(`${field} has an invalid length`);
  }

  const length = lengthDescriptor.value as number;
  const expectedKeys = new Set([
    "length",
    ...Array.from({ length }, (_, index) => String(index)),
  ]);
  const actualKeys = inspectOwnKeys(value, field);
  if (
    actualKeys.some(
      (key) => typeof key !== "string" || !expectedKeys.has(key),
    )
  ) {
    fail(`${field} contains unsupported fields`);
  }

  const snapshot: unknown[] = [];
  for (let index = 0; index < length; index += 1) {
    const descriptor = inspectOwnPropertyDescriptor(value, String(index), field);
    if (!descriptor || !("value" in descriptor) || !descriptor.enumerable) {
      fail(`${field} must not contain sparse entries`);
    }
    snapshot.push(descriptor.value);
  }
  return snapshot;
}

function parseConfiguration(value: unknown): AiFilterConfiguration {
  const record = snapshotDataRecord(value, "configuration");
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

  const queryText = normalizeAiFilterSoftQueryV1(record.queryText);

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
  const record = snapshotDataRecord(value, "candidate");
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
  const record = snapshotDataRecord(input, "segment request");
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
  const record = snapshotDataRecord(value, "decision");
  requireExactKeys(record, ["candidateId", "decision"], "decision");
  const decision = record.decision;
  if (decision !== "accepted" && decision !== "rejected") {
    fail("decision must be binary");
  }
  return Object.freeze({
    candidateId: requireUuid(record.candidateId, "decision.candidateId"),
    decision,
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
  const record = snapshotDataRecord(input, "terminal result");
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
