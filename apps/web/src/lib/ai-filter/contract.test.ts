import { describe, expect, it } from "vitest";

import {
  AI_FILTER_CONTRACT_VERSION,
  AI_FILTER_QUERY_MAX_LENGTH,
  AI_FILTER_SEGMENT_LIMIT,
  AiFilterContractError,
  assertSameAiFilterRunBinding,
  isAiFilterProductDecisionExpired,
  materializeAiFilterProductDecisions,
  parseAiFilterSegmentRequest,
  parseAiFilterTerminalResult,
  validateAiFilterConfigurationTransition,
} from "./contract";

const RUN_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const CONFIGURATION_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const WATCHLIST_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const CANDIDATE_ONE = "11111111-1111-1111-1111-111111111111";
const CANDIDATE_TWO = "22222222-2222-2222-2222-222222222222";
const FIRST_SEEN_ONE = "2026-08-15T12:00:00.000Z";
const FIRST_SEEN_TWO = "2026-08-16T12:00:00.000Z";
const EXPIRES_ONE = "2026-09-14T12:00:00.000Z";
const EXPIRES_TWO = "2026-09-15T12:00:00.000Z";
const REQUESTED_AT = "2026-09-01T12:00:00.000Z";

const baseConfiguration = {
  version: AI_FILTER_CONTRACT_VERSION,
  configurationId: CONFIGURATION_ID,
  ownerId: "user_01k4m8xafp6r6fj4qv2czp6njd",
  watchlistId: WATCHLIST_ID,
  candidateConstraint: "canonical_structured_watchlist" as const,
  queryText: "Senior backend roles with distributed systems ownership",
  queryRevision: 1,
  watchlistRevision: 1,
};

const baseRequest = {
  version: AI_FILTER_CONTRACT_VERSION,
  runId: RUN_ID,
  requestedAt: REQUESTED_AT,
  configuration: baseConfiguration,
  candidates: [
    {
      candidateId: CANDIDATE_ONE,
      postingFirstSeenAt: FIRST_SEEN_ONE,
      productExpiresAt: EXPIRES_ONE,
    },
    {
      candidateId: CANDIDATE_TWO,
      postingFirstSeenAt: FIRST_SEEN_TWO,
      productExpiresAt: EXPIRES_TWO,
    },
  ],
};

const completedResult = {
  status: "completed",
  decisions: [
    { candidateId: CANDIDATE_ONE, decision: "accepted" },
    { candidateId: CANDIDATE_TWO, decision: "rejected" },
  ],
};

describe("parseAiFilterSegmentRequest", () => {
  it("parses one bounded, frozen segment without granting execution", () => {
    expect(parseAiFilterSegmentRequest(baseRequest)).toEqual(baseRequest);
  });

  it("uses an opaque run ID that contains no query or owner material", () => {
    const parsed = parseAiFilterSegmentRequest(baseRequest);

    expect(parsed.runId).toBe(RUN_ID);
    expect(parsed.runId).not.toContain(baseConfiguration.queryText);
    expect(parsed.runId).not.toContain(baseConfiguration.ownerId);
    expect(parsed).not.toHaveProperty("idempotencyIdentity");
  });

  it("rejects empty, oversized, sparse, duplicate, and extra-field candidates", () => {
    expect(() =>
      parseAiFilterSegmentRequest({ ...baseRequest, candidates: [] }),
    ).toThrow(AiFilterContractError);

    const oversizedCandidates = Array.from(
      { length: AI_FILTER_SEGMENT_LIMIT + 1 },
      (_, index) => ({
        candidateId: `00000000-0000-0000-0000-${String(index).padStart(12, "0")}`,
        postingFirstSeenAt: FIRST_SEEN_ONE,
        productExpiresAt: EXPIRES_ONE,
      }),
    );
    expect(() =>
      parseAiFilterSegmentRequest({
        ...baseRequest,
        candidates: oversizedCandidates,
      }),
    ).toThrow(AiFilterContractError);

    expect(() =>
      parseAiFilterSegmentRequest({
        ...baseRequest,
        candidates: new Array(1),
      }),
    ).toThrow(/sparse/);

    expect(() =>
      parseAiFilterSegmentRequest({
        ...baseRequest,
        candidates: [baseRequest.candidates[0], baseRequest.candidates[0]],
      }),
    ).toThrow(/duplicate/);

    expect(() =>
      parseAiFilterSegmentRequest({
        ...baseRequest,
        candidates: [{ ...baseRequest.candidates[0], title: "must not leak" }],
      }),
    ).toThrow(/unsupported fields/);
  });

  it.each([
    "UPPERCASE-UUID",
    "11111111-1111-1111-1111-11111111111 ",
    " 11111111-1111-1111-1111-111111111111",
    "11111111-1111-1111-1111-11111111111\n",
    "11111111111111111111111111111111",
  ])("rejects a non-canonical candidate ID: %j", (candidateId) => {
    expect(() =>
      parseAiFilterSegmentRequest({
        ...baseRequest,
        candidates: [{ ...baseRequest.candidates[0], candidateId }],
      }),
    ).toThrow(/canonical lowercase UUID/);
  });

  it("rejects invalid query text and limits it before provider work", () => {
    for (const queryText of [
      "",
      " surrounded ",
      "control\u0000character",
      "q".repeat(AI_FILTER_QUERY_MAX_LENGTH + 1),
    ]) {
      expect(() =>
        parseAiFilterSegmentRequest({
          ...baseRequest,
          configuration: { ...baseConfiguration, queryText },
        }),
      ).toThrow(/queryText/);
    }
  });

  it("rejects expired, future, incorrect, non-canonical, and overflowing lifetimes", () => {
    expect(() =>
      parseAiFilterSegmentRequest({
        ...baseRequest,
        requestedAt: EXPIRES_ONE,
        candidates: [baseRequest.candidates[0]],
      }),
    ).toThrow(/expired/);

    expect(() =>
      parseAiFilterSegmentRequest({
        ...baseRequest,
        requestedAt: "2026-08-01T12:00:00.000Z",
        candidates: [baseRequest.candidates[0]],
      }),
    ).toThrow(/seen after/);

    expect(() =>
      parseAiFilterSegmentRequest({
        ...baseRequest,
        candidates: [
          {
            ...baseRequest.candidates[0],
            productExpiresAt: "2026-09-13T12:00:00.000Z",
          },
        ],
      }),
    ).toThrow(/retention boundary/);

    expect(() =>
      parseAiFilterSegmentRequest({
        ...baseRequest,
        requestedAt: "2026-09-01T12:00:00Z",
      }),
    ).toThrow(/canonical ISO instant/);

    const maximumInstant = new Date(8_640_000_000_000_000).toISOString();
    expect(() =>
      parseAiFilterSegmentRequest({
        ...baseRequest,
        requestedAt: maximumInstant,
        candidates: [
          {
            candidateId: CANDIDATE_ONE,
            postingFirstSeenAt: maximumInstant,
            productExpiresAt: maximumInstant,
          },
        ],
      }),
    ).toThrow(/out of range/);
  });
});

describe("validateAiFilterConfigurationTransition", () => {
  it("accepts an unchanged snapshot and independently revisioned changes", () => {
    expect(
      validateAiFilterConfigurationTransition(
        baseConfiguration,
        baseConfiguration,
        { watchlistChanged: false },
      ),
    ).toEqual(baseConfiguration);

    const next = {
      ...baseConfiguration,
      queryText: "Platform roles",
      queryRevision: 2,
      watchlistRevision: 2,
    };
    expect(
      validateAiFilterConfigurationTransition(baseConfiguration, next, {
        watchlistChanged: true,
      }),
    ).toEqual(next);
  });

  it("rejects identity mutation, revision rollback, and revisions detached from change", () => {
    expect(() =>
      validateAiFilterConfigurationTransition(
        baseConfiguration,
        { ...baseConfiguration, ownerId: "another_owner" },
        { watchlistChanged: false },
      ),
    ).toThrow(/immutable/);

    expect(() =>
      validateAiFilterConfigurationTransition(
        { ...baseConfiguration, queryRevision: 2 },
        baseConfiguration,
        { watchlistChanged: false },
      ),
    ).toThrow(/must not decrease/);

    expect(() =>
      validateAiFilterConfigurationTransition(
        baseConfiguration,
        { ...baseConfiguration, queryText: "Changed without revision" },
        { watchlistChanged: false },
      ),
    ).toThrow(/query revision/);

    expect(() =>
      validateAiFilterConfigurationTransition(
        baseConfiguration,
        { ...baseConfiguration, queryRevision: 2 },
        { watchlistChanged: false },
      ),
    ).toThrow(/query revision/);

    expect(() =>
      validateAiFilterConfigurationTransition(
        baseConfiguration,
        { ...baseConfiguration, watchlistRevision: 2 },
        { watchlistChanged: false },
      ),
    ).toThrow(/watchlist revision/);
  });
});

describe("assertSameAiFilterRunBinding", () => {
  it("accepts only an exact retry of the persisted snapshot", () => {
    expect(
      assertSameAiFilterRunBinding(baseRequest, structuredClone(baseRequest)),
    ).toEqual(baseRequest);
  });

  it("rejects reuse with reordered candidates or changed semantic data", () => {
    const reordered = structuredClone(baseRequest);
    reordered.candidates.reverse();
    expect(() => assertSameAiFilterRunBinding(baseRequest, reordered)).toThrow(
      /different snapshot/,
    );

    const changedQuery = structuredClone(baseRequest);
    changedQuery.configuration.queryText = "A different private query";
    changedQuery.configuration.queryRevision = 2;
    expect(() => assertSameAiFilterRunBinding(baseRequest, changedQuery)).toThrow(
      /different snapshot/,
    );

    const changedCandidate = structuredClone(baseRequest);
    changedCandidate.candidates[0].candidateId =
      "33333333-3333-3333-3333-333333333333";
    expect(() =>
      assertSameAiFilterRunBinding(baseRequest, changedCandidate),
    ).toThrow(/different snapshot/);

    expect(() =>
      assertSameAiFilterRunBinding(baseRequest, {
        ...baseRequest,
        runId: "dddddddd-dddd-dddd-dddd-dddddddddddd",
      }),
    ).toThrow(/do not match/);
  });
});

describe("parseAiFilterTerminalResult", () => {
  it("requires a complete, ordered, binary result with no extra fields", () => {
    expect(parseAiFilterTerminalResult(completedResult, baseRequest)).toEqual(
      completedResult,
    );

    expect(() =>
      parseAiFilterTerminalResult(
        { status: "completed", decisions: [completedResult.decisions[0]] },
        baseRequest,
      ),
    ).toThrow(/every candidate/);

    expect(() =>
      parseAiFilterTerminalResult(
        {
          status: "completed",
          decisions: [
            completedResult.decisions[1],
            completedResult.decisions[0],
          ],
        },
        baseRequest,
      ),
    ).toThrow(/snapshot order/);

    expect(() =>
      parseAiFilterTerminalResult(
        {
          status: "completed",
          decisions: [
            { ...completedResult.decisions[0], confidence: 0.9 },
            completedResult.decisions[1],
          ],
        },
        baseRequest,
      ),
    ).toThrow(/unsupported fields/);
  });

  it("rejects foreign, duplicate, non-binary, and sparse decisions", () => {
    expect(() =>
      parseAiFilterTerminalResult(
        {
          status: "completed",
          decisions: [
            {
              candidateId: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
              decision: "accepted",
            },
            completedResult.decisions[1],
          ],
        },
        baseRequest,
      ),
    ).toThrow(/foreign/);

    expect(() =>
      parseAiFilterTerminalResult(
        {
          status: "completed",
          decisions: [
            completedResult.decisions[0],
            completedResult.decisions[0],
          ],
        },
        baseRequest,
      ),
    ).toThrow(/duplicate/);

    expect(() =>
      parseAiFilterTerminalResult(
        {
          status: "completed",
          decisions: [
            { candidateId: CANDIDATE_ONE, decision: "maybe" },
            completedResult.decisions[1],
          ],
        },
        baseRequest,
      ),
    ).toThrow(/binary/);

    expect(() =>
      parseAiFilterTerminalResult(
        { status: "completed", decisions: new Array(2) },
        baseRequest,
      ),
    ).toThrow(/sparse/);
  });

  it("preserves valid paid work when a run stops", () => {
    const stopped = {
      status: "stopped",
      stopReason: "budget_exhausted",
      decisions: [completedResult.decisions[1]],
    };
    expect(parseAiFilterTerminalResult(stopped, baseRequest)).toEqual(stopped);

    expect(
      parseAiFilterTerminalResult(
        {
          status: "stopped",
          stopReason: "provider_unavailable",
          decisions: [],
        },
        baseRequest,
      ),
    ).toEqual({
      status: "stopped",
      stopReason: "provider_unavailable",
      decisions: [],
    });

    expect(() =>
      parseAiFilterTerminalResult(
        {
          status: "stopped",
          stopReason: "budget_exhausted",
          decisions: [
            completedResult.decisions[1],
            completedResult.decisions[0],
          ],
        },
        baseRequest,
      ),
    ).toThrow(/snapshot order/);
  });
});

describe("product decision retention", () => {
  it("attaches run, owner, revisions, and the posting-derived lifetime", () => {
    const decisions = materializeAiFilterProductDecisions(
      baseRequest,
      {
        status: "stopped",
        stopReason: "configuration_changed",
        decisions: [completedResult.decisions[0]],
      },
      "2026-09-01T12:00:01.000Z",
    );

    expect(decisions).toEqual([
      {
        version: AI_FILTER_CONTRACT_VERSION,
        runId: RUN_ID,
        configurationId: CONFIGURATION_ID,
        ownerId: baseConfiguration.ownerId,
        watchlistId: WATCHLIST_ID,
        queryRevision: 1,
        watchlistRevision: 1,
        candidateId: CANDIDATE_ONE,
        decision: "accepted",
        postingFirstSeenAt: FIRST_SEEN_ONE,
        decidedAt: "2026-09-01T12:00:01.000Z",
        expiresAt: EXPIRES_ONE,
      },
    ]);
  });

  it("uses an inclusive expiry boundary and rejects non-canonical instants", () => {
    expect(
      isAiFilterProductDecisionExpired(
        { expiresAt: EXPIRES_ONE },
        "2026-09-14T11:59:59.999Z",
      ),
    ).toBe(false);
    expect(
      isAiFilterProductDecisionExpired(
        { expiresAt: EXPIRES_ONE },
        EXPIRES_ONE,
      ),
    ).toBe(true);
    expect(() =>
      isAiFilterProductDecisionExpired(
        { expiresAt: EXPIRES_ONE },
        "2026-09-14T12:00:00Z",
      ),
    ).toThrow(/canonical ISO instant/);
  });

  it("rejects a decision timestamp before the frozen run", () => {
    expect(() =>
      materializeAiFilterProductDecisions(
        baseRequest,
        completedResult,
        "2026-09-01T11:59:59.999Z",
      ),
    ).toThrow(/cannot precede/);
  });
});
