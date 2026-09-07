export const AI_FILTER_CONTRACT_VERSION = 1 as const;
export const AI_FILTER_INITIAL_SEGMENT_LIMIT = 50 as const;
export const AI_FILTER_PRODUCT_RETENTION_DAYS = 30 as const;

const AI_FILTER_PRODUCT_RETENTION_MS =
  AI_FILTER_PRODUCT_RETENTION_DAYS * 24 * 60 * 60 * 1_000;

export type AiFilterContractVersion = typeof AI_FILTER_CONTRACT_VERSION;
export type AiFilterWorkloadKind = "manual_initial_segment";
export type AiFilterDecisionValue = "accepted" | "rejected";

export type AiFilterConfiguration = Readonly<{
  version: AiFilterContractVersion;
  configurationId: string;
  ownerId: string;
  watchlistId: string;
  candidateConstraint: "canonical_structured_watchlist";
  queryText: string;
  queryRevision: number;
  watchlistRevision: number;
}>;

export type AiFilterSegmentRequest = Readonly<{
  version: AiFilterContractVersion;
  idempotencyIdentity: string;
  configurationId: string;
  ownerId: string;
  watchlistId: string;
  candidateConstraint: "canonical_structured_watchlist";
  queryText: string;
  queryRevision: number;
  watchlistRevision: number;
  workloadKind: AiFilterWorkloadKind;
  candidateIds: readonly string[];
  candidateCount: number;
}>;

export type AiFilterDecision = Readonly<{
  candidateId: string;
  decision: AiFilterDecisionValue;
}>;

export type AiFilterStopReason =
  | "entitlement_unavailable"
  | "watchlist_too_broad"
  | "budget_exhausted"
  | "kill_switch_active"
  | "cancelled";

export type AiFilterSegmentTerminalState =
  | Readonly<{
      version: AiFilterContractVersion;
      status: "completed";
      requestIdentity: string;
      decisions: readonly AiFilterDecision[];
    }>
  | Readonly<{
      version: AiFilterContractVersion;
      status: "stopped";
      requestIdentity: string;
      reason: AiFilterStopReason;
    }>;

export type AiFilterProductLifetime = Readonly<{
  postingFirstSeenAt: string;
  expiresAt: string;
}>;

export type RuntimeSchema<T> = Readonly<{
  parse: (input: unknown) => T;
}>;

export class AiFilterContractError extends TypeError {
  override name = "AiFilterContractError";
}

/** Deliberately identical for anonymous and cross-owner access. */
export class AiFilterNotFoundError extends Error {
  override name = "AiFilterNotFoundError";

  constructor() {
    super("AI filter resource not found");
  }
}

function contractError(message: string): never {
  throw new AiFilterContractError(message);
}

function record(value: unknown, name: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return contractError(`${name} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  name: string,
): void {
  const keys = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (
    keys.length !== wanted.length ||
    keys.some((key, index) => key !== wanted[index])
  ) {
    contractError(`${name} has unexpected or missing fields`);
  }
}

function nonBlankString(value: unknown, name: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    return contractError(`${name} must be a non-blank string`);
  }
  return value;
}

function positiveRevision(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    return contractError(`${name} must be a positive safe integer`);
  }
  return value as number;
}

function literal<T extends string | number>(
  value: unknown,
  expected: T,
  name: string,
): T {
  if (value !== expected) {
    return contractError(`${name} must be ${JSON.stringify(expected)}`);
  }
  return expected;
}

function parseCandidateIds(value: unknown): readonly string[] {
  if (!Array.isArray(value) || value.length === 0) {
    return contractError("candidateIds must contain at least one candidate");
  }
  if (value.length > AI_FILTER_INITIAL_SEGMENT_LIMIT) {
    return contractError(
      `candidateIds cannot exceed ${AI_FILTER_INITIAL_SEGMENT_LIMIT}`,
    );
  }

  const candidateIds = value.map((candidateId, index) =>
    nonBlankString(candidateId, `candidateIds[${index}]`),
  );
  if (new Set(candidateIds).size !== candidateIds.length) {
    return contractError("candidateIds must be unique");
  }
  return candidateIds;
}

function parseConfiguration(input: unknown): AiFilterConfiguration {
  const value = record(input, "configuration");
  exactKeys(
    value,
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

  return {
    version: literal(value.version, AI_FILTER_CONTRACT_VERSION, "version"),
    configurationId: nonBlankString(
      value.configurationId,
      "configurationId",
    ),
    ownerId: nonBlankString(value.ownerId, "ownerId"),
    watchlistId: nonBlankString(value.watchlistId, "watchlistId"),
    candidateConstraint: literal(
      value.candidateConstraint,
      "canonical_structured_watchlist",
      "candidateConstraint",
    ),
    queryText: nonBlankString(value.queryText, "queryText"),
    queryRevision: positiveRevision(value.queryRevision, "queryRevision"),
    watchlistRevision: positiveRevision(
      value.watchlistRevision,
      "watchlistRevision",
    ),
  };
}

export const aiFilterConfigurationSchema: RuntimeSchema<AiFilterConfiguration> =
  { parse: parseConfiguration };

export function requireAiFilterOwner<T extends { readonly ownerId: string }>(
  resource: T,
  actorId: string | null | undefined,
): T {
  if (!actorId || actorId !== resource.ownerId) {
    throw new AiFilterNotFoundError();
  }
  return resource;
}

function idempotencyIdentity(
  configuration: AiFilterConfiguration,
  candidateIds: readonly string[],
): string {
  // JSON encoding preserves candidate order and avoids delimiter collisions.
  return `ai-filter:${AI_FILTER_CONTRACT_VERSION}:${JSON.stringify([
    configuration.configurationId,
    configuration.ownerId,
    configuration.watchlistId,
    configuration.queryText,
    configuration.queryRevision,
    configuration.watchlistRevision,
    "manual_initial_segment",
    candidateIds,
  ])}`;
}

export function createAiFilterSegmentRequest(input: {
  configuration: AiFilterConfiguration;
  actorId: string | null | undefined;
  candidateIds: readonly string[];
}): AiFilterSegmentRequest {
  const configuration = aiFilterConfigurationSchema.parse(input.configuration);
  requireAiFilterOwner(configuration, input.actorId);
  const candidateIds = parseCandidateIds(input.candidateIds);

  return {
    ...configuration,
    workloadKind: "manual_initial_segment",
    candidateIds,
    candidateCount: candidateIds.length,
    idempotencyIdentity: idempotencyIdentity(configuration, candidateIds),
  };
}

function parseSegmentRequest(input: unknown): AiFilterSegmentRequest {
  const value = record(input, "segment request");
  exactKeys(
    value,
    [
      "version",
      "idempotencyIdentity",
      "configurationId",
      "ownerId",
      "watchlistId",
      "candidateConstraint",
      "queryText",
      "queryRevision",
      "watchlistRevision",
      "workloadKind",
      "candidateIds",
      "candidateCount",
    ],
    "segment request",
  );

  const configuration = parseConfiguration({
    version: value.version,
    configurationId: value.configurationId,
    ownerId: value.ownerId,
    watchlistId: value.watchlistId,
    candidateConstraint: value.candidateConstraint,
    queryText: value.queryText,
    queryRevision: value.queryRevision,
    watchlistRevision: value.watchlistRevision,
  });
  literal(value.workloadKind, "manual_initial_segment", "workloadKind");
  const candidateIds = parseCandidateIds(value.candidateIds);
  if (value.candidateCount !== candidateIds.length) {
    contractError("candidateCount must equal candidateIds.length");
  }

  const expectedIdentity = idempotencyIdentity(configuration, candidateIds);
  if (value.idempotencyIdentity !== expectedIdentity) {
    contractError("idempotencyIdentity does not match request content");
  }

  return {
    ...configuration,
    workloadKind: "manual_initial_segment",
    candidateIds,
    candidateCount: candidateIds.length,
    idempotencyIdentity: expectedIdentity,
  };
}

export const aiFilterSegmentRequestSchema: RuntimeSchema<AiFilterSegmentRequest> =
  { parse: parseSegmentRequest };

function parseDecision(value: unknown, index: number): AiFilterDecision {
  const decision = record(value, `decisions[${index}]`);
  exactKeys(decision, ["candidateId", "decision"], `decisions[${index}]`);
  const candidateId = nonBlankString(
    decision.candidateId,
    `decisions[${index}].candidateId`,
  );
  if (decision.decision !== "accepted" && decision.decision !== "rejected") {
    contractError(
      `decisions[${index}].decision must be accepted or rejected`,
    );
  }
  return { candidateId, decision: decision.decision };
}

function parseCompletedState(
  value: Record<string, unknown>,
  request: AiFilterSegmentRequest,
): AiFilterSegmentTerminalState {
  exactKeys(
    value,
    ["version", "status", "requestIdentity", "decisions"],
    "completed state",
  );
  literal(value.version, AI_FILTER_CONTRACT_VERSION, "version");
  if (value.requestIdentity !== request.idempotencyIdentity) {
    contractError("requestIdentity does not match the segment request");
  }
  if (!Array.isArray(value.decisions)) {
    contractError("decisions must be an array");
  }
  if (value.decisions.length !== request.candidateCount) {
    contractError("decisions must match the requested candidate cardinality");
  }

  const byCandidateId = new Map<string, AiFilterDecision>();
  for (const [index, rawDecision] of value.decisions.entries()) {
    const decision = parseDecision(rawDecision, index);
    if (byCandidateId.has(decision.candidateId)) {
      contractError("decisions must contain unique candidate IDs");
    }
    byCandidateId.set(decision.candidateId, decision);
  }

  const decisions = request.candidateIds.map((candidateId) => {
    const decision = byCandidateId.get(candidateId);
    if (!decision) {
      return contractError(
        "decisions must contain exactly the requested candidate IDs",
      );
    }
    return decision;
  });
  if (byCandidateId.size !== request.candidateIds.length) {
    contractError("decisions must contain exactly the requested candidate IDs");
  }

  return {
    version: AI_FILTER_CONTRACT_VERSION,
    status: "completed",
    requestIdentity: request.idempotencyIdentity,
    decisions,
  };
}

const STOP_REASONS: ReadonlySet<string> = new Set<AiFilterStopReason>([
  "entitlement_unavailable",
  "watchlist_too_broad",
  "budget_exhausted",
  "kill_switch_active",
  "cancelled",
]);

function parseStoppedState(
  value: Record<string, unknown>,
  request: AiFilterSegmentRequest,
): AiFilterSegmentTerminalState {
  exactKeys(
    value,
    ["version", "status", "requestIdentity", "reason"],
    "stopped state",
  );
  literal(value.version, AI_FILTER_CONTRACT_VERSION, "version");
  if (value.requestIdentity !== request.idempotencyIdentity) {
    contractError("requestIdentity does not match the segment request");
  }
  if (typeof value.reason !== "string" || !STOP_REASONS.has(value.reason)) {
    contractError("reason is not a supported AI filter stop reason");
  }
  return {
    version: AI_FILTER_CONTRACT_VERSION,
    status: "stopped",
    requestIdentity: request.idempotencyIdentity,
    reason: value.reason as AiFilterStopReason,
  };
}

export function parseAiFilterSegmentTerminalState(
  input: unknown,
  requestInput: AiFilterSegmentRequest,
): AiFilterSegmentTerminalState {
  const request = aiFilterSegmentRequestSchema.parse(requestInput);
  const value = record(input, "terminal state");
  if (value.status === "completed") {
    return parseCompletedState(value, request);
  }
  if (value.status === "stopped") {
    return parseStoppedState(value, request);
  }
  return contractError("terminal state must be completed or stopped");
}

function canonicalInstant(value: string | Date, name: string): string {
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isFinite(date.getTime())) {
    return contractError(`${name} must be a valid instant`);
  }
  return date.toISOString();
}

export function createAiFilterProductLifetime(
  postingFirstSeenAt: string | Date,
): AiFilterProductLifetime {
  const firstSeen = canonicalInstant(postingFirstSeenAt, "postingFirstSeenAt");
  return {
    postingFirstSeenAt: firstSeen,
    expiresAt: new Date(
      new Date(firstSeen).getTime() + AI_FILTER_PRODUCT_RETENTION_MS,
    ).toISOString(),
  };
}

function parseProductLifetime(input: unknown): AiFilterProductLifetime {
  const value = record(input, "product lifetime");
  exactKeys(
    value,
    ["postingFirstSeenAt", "expiresAt"],
    "product lifetime",
  );
  const expected = createAiFilterProductLifetime(
    nonBlankString(value.postingFirstSeenAt, "postingFirstSeenAt"),
  );
  const expiresAt = canonicalInstant(
    nonBlankString(value.expiresAt, "expiresAt"),
    "expiresAt",
  );
  if (expiresAt !== expected.expiresAt) {
    contractError("expiresAt must be exactly postingFirstSeenAt plus 30 days");
  }
  return expected;
}

export const aiFilterProductLifetimeSchema: RuntimeSchema<AiFilterProductLifetime> =
  { parse: parseProductLifetime };

export function isAiFilterProductExpired(
  lifetimeInput: AiFilterProductLifetime,
  now: string | Date,
): boolean {
  const lifetime = aiFilterProductLifetimeSchema.parse(lifetimeInput);
  const current = canonicalInstant(now, "now");
  return new Date(current).getTime() >= new Date(lifetime.expiresAt).getTime();
}
