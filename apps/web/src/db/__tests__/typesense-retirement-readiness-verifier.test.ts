import { describe, expect, it, vi } from "vitest";

import {
  MAX_MULTI_SEARCHES,
  buildPostingSearchPlan,
  evaluatePostingReadiness,
  failureEvidence,
  persistEvidence,
  validateDatabaseUrl,
} from "../../../scripts/verify-typesense-retirement-readiness";

describe("Typesense retirement readiness verifier", () => {
  it("uses Supabase only as a one-search frozen posting coverage floor", () => {
    expect(MAX_MULTI_SEARCHES).toBe(1);
    expect(buildPostingSearchPlan()).toEqual([
      { collection: "job_posting", q: "*", query_by: "title", per_page: 0 },
    ]);
    expect(evaluatePostingReadiness(25, 30, "2026-08-04T00:00:00.000Z")).toMatchObject({
      status: "passed",
      ready: true,
      sourceAuthority: {
        postingFloor: "frozen_supabase_job_posting",
        taxonomies: "crawler_local_postgres_attestation",
      },
      posting: {
        frozenSourceRows: 25,
        typesenseDocuments: 30,
        coverageDelta: 5,
        coverageNonRegressing: true,
      },
      requestBudget: {
        httpRequests: 1,
        multiSearches: 1,
        maximumHttpRequests: 1,
        maximumMultiSearches: 1,
      },
      failures: [],
    });
  });

  it("fails on an empty or regressing posting index", () => {
    expect(evaluatePostingReadiness(25, 24).failures).toContainEqual(
      expect.objectContaining({
        scope: "job_posting",
        kind: "coverage_regression",
        minimum: 25,
        actual: 24,
        delta: -1,
      }),
    );
    expect(evaluatePostingReadiness(25, 0).failures).toContainEqual({
      scope: "job_posting",
      kind: "empty_typesense",
    });
    expect(evaluatePostingReadiness(0, 1).failures).toContainEqual({
      scope: "job_posting",
      kind: "empty_source",
    });
  });

  it("rejects the transaction pooler and invalid document counts", () => {
    expect(() =>
      validateDatabaseUrl("postgresql://readonly@example.test:6543/postgres"),
    ).toThrow(/transaction pooler/);
    expect(() => validateDatabaseUrl("not a url")).toThrow(/valid URL/);
    expect(() => evaluatePostingReadiness(-1, 1)).toThrow(/invalid document count/);
  });

  it("writes identical structured redacted failure evidence to file and stdout", () => {
    const writes: { path?: string; rendered: string }[] = [];
    const evidence = failureEvidence(
      new Error("postgresql://user:secret@example.test/private"),
      "2026-08-04T00:00:00.000Z",
    );
    const writer = {
      writeOutput: vi.fn((path: string, rendered: string) => {
        writes.push({ path, rendered });
      }),
      writeStdout: vi.fn((rendered: string) => {
        writes.push({ rendered });
      }),
    };

    const rendered = persistEvidence(evidence, "evidence.json", writer);

    expect(writes).toEqual([
      { path: "evidence.json", rendered },
      { rendered },
    ]);
    expect(JSON.parse(rendered)).toMatchObject({
      status: "failed",
      ready: false,
      posting: null,
      failures: [
        {
          scope: "verifier",
          kind: "guard",
          message: "Live retirement readiness verification could not complete",
        },
      ],
    });
    expect(rendered).not.toContain("user:secret");
  });
});
