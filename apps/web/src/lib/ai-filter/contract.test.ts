import { describe, expect, expectTypeOf, it } from "vitest";

import {
  AI_FILTER_CONTRACT_VERSION,
  AI_FILTER_INITIAL_SEGMENT_LIMIT,
  AiFilterContractError,
  AiFilterNotFoundError,
  aiFilterConfigurationSchema,
  aiFilterProductLifetimeSchema,
  aiFilterSegmentRequestSchema,
  createAiFilterProductLifetime,
  createAiFilterSegmentRequest,
  isAiFilterProductExpired,
  parseAiFilterSegmentTerminalState,
  requireAiFilterOwner,
  type AiFilterConfiguration,
  type AiFilterDecisionValue,
  type AiFilterStopReason,
  type AiFilterWorkloadKind,
} from "./contract";

const configuration: AiFilterConfiguration = {
  version: AI_FILTER_CONTRACT_VERSION,
  configurationId: "config-1",
  ownerId: "owner-1",
  watchlistId: "watchlist-1",
  candidateConstraint: "canonical_structured_watchlist",
  queryText: "Backend roles where I can own platform reliability",
  queryRevision: 2,
  watchlistRevision: 7,
};

function request(candidateIds: readonly string[] = ["job-3", "job-2", "job-1"]) {
  return createAiFilterSegmentRequest({
    configuration,
    actorId: configuration.ownerId,
    candidateIds,
  });
}

describe("AI filter v1 configuration and request", () => {
  it("pins the only MVP workload and binary decision vocabulary", () => {
    expectTypeOf<AiFilterWorkloadKind>().toEqualTypeOf<
      "manual_initial_segment"
    >();
    expectTypeOf<AiFilterDecisionValue>().toEqualTypeOf<
      "accepted" | "rejected"
    >();
    expectTypeOf<AiFilterStopReason>().toEqualTypeOf<
      | "entitlement_unavailable"
      | "watchlist_too_broad"
      | "budget_exhausted"
      | "kill_switch_active"
      | "cancelled"
    >();
  });

  it("keeps the canonical structured watchlist hard and revisions explicit", () => {
    const parsed = aiFilterConfigurationSchema.parse(configuration);
    const segment = request();

    expect(parsed.candidateConstraint).toBe("canonical_structured_watchlist");
    expect(segment).toMatchObject({
      version: 1,
      ownerId: "owner-1",
      watchlistId: "watchlist-1",
      queryRevision: 2,
      watchlistRevision: 7,
      workloadKind: "manual_initial_segment",
      candidateIds: ["job-3", "job-2", "job-1"],
      candidateCount: 3,
    });
  });

  it("fails closed with the same not-found error for anonymous and other owners", () => {
    expect(() => requireAiFilterOwner(configuration, null)).toThrow(
      AiFilterNotFoundError,
    );
    expect(() => requireAiFilterOwner(configuration, "owner-2")).toThrow(
      AiFilterNotFoundError,
    );
    expect(() =>
      createAiFilterSegmentRequest({
        configuration,
        actorId: "owner-2",
        candidateIds: ["job-1"],
      }),
    ).toThrow("AI filter resource not found");
    expect(requireAiFilterOwner(configuration, "owner-1")).toBe(configuration);
  });

  it("rejects unknown versions, invalid revisions, and extra fields", () => {
    expect(() =>
      aiFilterConfigurationSchema.parse({ ...configuration, version: 2 }),
    ).toThrow(AiFilterContractError);
    expect(() =>
      aiFilterConfigurationSchema.parse({ ...configuration, queryRevision: 0 }),
    ).toThrow("queryRevision must be a positive safe integer");
    expect(() =>
      aiFilterConfigurationSchema.parse({
        ...configuration,
        publicWatchlistId: "not-supported",
      }),
    ).toThrow("unexpected or missing fields");
  });

  it("accepts one bounded segment and rejects empty, duplicate, or oversized input", () => {
    expect(
      request(Array.from({ length: 50 }, (_, index) => `job-${index}`))
        .candidateCount,
    ).toBe(AI_FILTER_INITIAL_SEGMENT_LIMIT);
    expect(() => request([])).toThrow("at least one candidate");
    expect(() => request(["job-1", "job-1"])).toThrow("must be unique");
    expect(() =>
      request(Array.from({ length: 51 }, (_, index) => `job-${index}`)),
    ).toThrow("cannot exceed 50");
  });

  it("uses a deterministic identity that changes with request semantics", () => {
    const first = request(["job-2", "job-1"]);
    const retry = request(["job-2", "job-1"]);
    const reordered = request(["job-1", "job-2"]);
    const revised = createAiFilterSegmentRequest({
      configuration: { ...configuration, queryRevision: 3 },
      actorId: configuration.ownerId,
      candidateIds: ["job-2", "job-1"],
    });

    expect(retry.idempotencyIdentity).toBe(first.idempotencyIdentity);
    expect(reordered.idempotencyIdentity).not.toBe(first.idempotencyIdentity);
    expect(revised.idempotencyIdentity).not.toBe(first.idempotencyIdentity);
  });

  it("rejects tampered count, workload kind, or identity at a boundary", () => {
    const segment = request();
    expect(() =>
      aiFilterSegmentRequestSchema.parse({ ...segment, candidateCount: 2 }),
    ).toThrow("candidateCount must equal candidateIds.length");
    expect(() =>
      aiFilterSegmentRequestSchema.parse({
        ...segment,
        workloadKind: "history_scroll",
      }),
    ).toThrow("workloadKind must be");
    expect(() =>
      aiFilterSegmentRequestSchema.parse({
        ...segment,
        idempotencyIdentity: "retry-anything",
      }),
    ).toThrow("idempotencyIdentity does not match request content");
  });
});

describe("AI filter v1 terminal states", () => {
  it("accepts all and only requested candidate IDs and restores request order", () => {
    const segment = request();
    const terminal = parseAiFilterSegmentTerminalState(
      {
        version: 1,
        status: "completed",
        requestIdentity: segment.idempotencyIdentity,
        decisions: [
          { candidateId: "job-1", decision: "rejected" },
          { candidateId: "job-3", decision: "accepted" },
          { candidateId: "job-2", decision: "accepted" },
        ],
      },
      segment,
    );

    expect(terminal).toEqual({
      version: 1,
      status: "completed",
      requestIdentity: segment.idempotencyIdentity,
      decisions: [
        { candidateId: "job-3", decision: "accepted" },
        { candidateId: "job-2", decision: "accepted" },
        { candidateId: "job-1", decision: "rejected" },
      ],
    });
  });

  it.each([
    {
      name: "missing",
      decisions: [
        { candidateId: "job-3", decision: "accepted" },
        { candidateId: "job-2", decision: "rejected" },
      ],
    },
    {
      name: "extra",
      decisions: [
        { candidateId: "job-3", decision: "accepted" },
        { candidateId: "job-2", decision: "rejected" },
        { candidateId: "job-1", decision: "accepted" },
        { candidateId: "job-0", decision: "accepted" },
      ],
    },
    {
      name: "duplicate",
      decisions: [
        { candidateId: "job-3", decision: "accepted" },
        { candidateId: "job-3", decision: "rejected" },
        { candidateId: "job-1", decision: "accepted" },
      ],
    },
    {
      name: "foreign",
      decisions: [
        { candidateId: "job-3", decision: "accepted" },
        { candidateId: "job-2", decision: "rejected" },
        { candidateId: "someone-elses-job", decision: "accepted" },
      ],
    },
  ])("rejects $name candidate output", ({ decisions }) => {
    const segment = request();
    expect(() =>
      parseAiFilterSegmentTerminalState(
        {
          version: 1,
          status: "completed",
          requestIdentity: segment.idempotencyIdentity,
          decisions,
        },
        segment,
      ),
    ).toThrow(AiFilterContractError);
  });

  it("rejects non-binary or enriched model output", () => {
    const segment = request(["job-1"]);
    expect(() =>
      parseAiFilterSegmentTerminalState(
        {
          version: 1,
          status: "completed",
          requestIdentity: segment.idempotencyIdentity,
          decisions: [{ candidateId: "job-1", decision: "maybe" }],
        },
        segment,
      ),
    ).toThrow("must be accepted or rejected");
    expect(() =>
      parseAiFilterSegmentTerminalState(
        {
          version: 1,
          status: "completed",
          requestIdentity: segment.idempotencyIdentity,
          decisions: [
            {
              candidateId: "job-1",
              decision: "rejected",
              explanation: "Not enough platform work",
            },
          ],
        },
        segment,
      ),
    ).toThrow("unexpected or missing fields");
  });

  it.each<AiFilterStopReason>([
    "entitlement_unavailable",
    "watchlist_too_broad",
    "budget_exhausted",
    "kill_switch_active",
    "cancelled",
  ])("accepts the typed %s stop without partial decisions", (reason) => {
    const segment = request();
    expect(
      parseAiFilterSegmentTerminalState(
        {
          version: 1,
          status: "stopped",
          requestIdentity: segment.idempotencyIdentity,
          reason,
        },
        segment,
      ),
    ).toEqual({
      version: 1,
      status: "stopped",
      requestIdentity: segment.idempotencyIdentity,
      reason,
    });
  });

  it("rejects unknown stops, mismatched requests, and partial stop output", () => {
    const segment = request();
    expect(() =>
      parseAiFilterSegmentTerminalState(
        {
          version: 1,
          status: "stopped",
          requestIdentity: segment.idempotencyIdentity,
          reason: "provider_error",
        },
        segment,
      ),
    ).toThrow("not a supported AI filter stop reason");
    expect(() =>
      parseAiFilterSegmentTerminalState(
        {
          version: 1,
          status: "stopped",
          requestIdentity: "another-request",
          reason: "cancelled",
        },
        segment,
      ),
    ).toThrow("does not match the segment request");
    expect(() =>
      parseAiFilterSegmentTerminalState(
        {
          version: 1,
          status: "stopped",
          requestIdentity: segment.idempotencyIdentity,
          reason: "budget_exhausted",
          decisions: [{ candidateId: "job-3", decision: "accepted" }],
        },
        segment,
      ),
    ).toThrow("unexpected or missing fields");
  });
});

describe("AI filter product lifetime", () => {
  it("expires exactly 30 days after posting first-seen", () => {
    const lifetime = createAiFilterProductLifetime(
      "2026-01-31T23:30:00.000Z",
    );
    expect(lifetime).toEqual({
      postingFirstSeenAt: "2026-01-31T23:30:00.000Z",
      expiresAt: "2026-03-02T23:30:00.000Z",
    });
    expect(
      isAiFilterProductExpired(lifetime, "2026-03-02T23:29:59.999Z"),
    ).toBe(false);
    expect(
      isAiFilterProductExpired(lifetime, "2026-03-02T23:30:00.000Z"),
    ).toBe(true);
  });

  it("cannot slide expiry on retry, read, disable, or entitlement resume", () => {
    const original = createAiFilterProductLifetime(
      "2026-06-01T00:00:00.000Z",
    );
    const retry = createAiFilterProductLifetime(original.postingFirstSeenAt);

    expect(retry.expiresAt).toBe(original.expiresAt);
    expect(() =>
      aiFilterProductLifetimeSchema.parse({
        ...original,
        expiresAt: "2026-08-01T00:00:00.000Z",
      }),
    ).toThrow("exactly postingFirstSeenAt plus 30 days");
  });

  it("rejects non-instants and extra retention inputs", () => {
    expect(() => createAiFilterProductLifetime("not-a-date")).toThrow(
      "must be a valid instant",
    );
    expect(() =>
      aiFilterProductLifetimeSchema.parse({
        ...createAiFilterProductLifetime("2026-06-01T00:00:00.000Z"),
        lastAccessedAt: "2026-06-29T00:00:00.000Z",
      }),
    ).toThrow("unexpected or missing fields");
  });
});
