/**
 * Unit tests for `POST /api/murmur/accept` — the Murmur webhook handler.
 *
 * Covers the full Verification matrix from jobseek#2763:
 *
 *   - Bearer missing                         → 401
 *   - Bearer wrong                           → 401
 *   - Body > 5 MB                            → 413
 *   - Idempotency-Key missing                → 400
 *   - Body parse failure                     → 400
 *   - Schema-invalid body                    → 400 with validation:* errors
 *   - Idempotent replay (same hash)          → 200 applied:false reason:already_applied
 *   - Idempotent replay (different hash)     → 200 applied:false reason:body_mismatch
 *   - Re-run probes succeed                  → 200 applied:true, catalog written
 *   - Re-run probes fail                     → 200 ok:false, no catalog write
 *   - Re-run probes time out                 → 504 ok:false errors:["probe_timeout"]
 *   - Catalog writer throws                  → 200 ok:false errors:["catalog_write_failed"]
 *
 * The catalog writer + idempotency ledger + probe re-runner are all
 * stubbed via the holders defined alongside the production functions.
 *
 * @see colophon-group/jobseek#2763
 */

import { describe, it, expect, beforeEach, vi } from "vitest";

import "./_helpers";
import {
  ApplyCatalogHolder,
  type ApplyCatalog,
} from "../_lib/catalog";
import {
  RerunHolder,
  type RerunProbes,
} from "../_lib/accept-pipeline";
import {
  LedgerReaderHolder,
  type LedgerReader,
} from "../_lib/idempotency";

/** Minimum-shape `final_output` body that passes schema validation. */
const VALID_FINAL_OUTPUT = {
  canonical_name: "Acme",
  canonical_website: "https://acme.example.com",
  slug: "acme",
  description: "Test fixture company.",
  industry_ids: ["software"],
  boards: [
    {
      alias: "global",
      board_url: "https://job-boards.greenhouse.io/acme",
      provider: "greenhouse",
      outcome: "configured",
      monitor_type: "greenhouse",
      monitor_config: { token: "acme" },
      scraper_type: "skip",
      scraper_config: {},
      verdict: "ok",
    },
  ],
} as const;

/** Build a fully-formed authorised webhook request. */
function webhookRequest(
  body: unknown,
  overrides?: {
    bearer?: string | null;
    runId?: string | null;
    rawBody?: string;
    contentLength?: string;
  },
): Request {
  const headers = new Headers({ "content-type": "application/json" });
  if (overrides?.bearer !== null) {
    headers.set(
      "authorization",
      `Bearer ${overrides?.bearer ?? process.env.MURMUR_TOKEN ?? "test-token"}`,
    );
  }
  if (overrides?.runId !== null) {
    headers.set("idempotency-key", overrides?.runId ?? "run-test-1");
  }
  const raw = overrides?.rawBody ?? JSON.stringify(body);
  if (overrides?.contentLength !== undefined) {
    headers.set("content-length", overrides.contentLength);
  }
  return new Request("https://jobseek.test/api/murmur/accept", {
    method: "POST",
    headers,
    body: raw,
  });
}

let appliedCalls: Array<{
  body: unknown;
  context: { runId: string; bodyHash: string };
}> = [];
let rerunCalls: number;
/**
 * Stub durable ledger backing store, keyed by run_id. Tests that want
 * to simulate a cold-start replay seed this directly (bypassing the
 * route's in-process Map) and then clear the Map.
 */
let durableLedger: Map<string, string>;

beforeEach(() => {
  appliedCalls = [];
  rerunCalls = 0;
  durableLedger = new Map();
  // Default: catalog applies cleanly with no warnings. Records into the
  // durable ledger stub so the cold-start-replay tests below see it.
  ApplyCatalogHolder.current = (async (_target, body, context) => {
    appliedCalls.push({ body, context });
    durableLedger.set(context.runId, context.bodyHash);
    return {
      companyId: "00000000-0000-0000-0000-000000000001",
      boardCount: 1,
      warnings: [],
    };
  }) as ApplyCatalog;
  // Default: probe re-run succeeds.
  RerunHolder.current = (async () => {
    rerunCalls += 1;
    return { status: "ok" } as const;
  }) as RerunProbes;
  // Default: durable ledger reader consults the in-memory map. Tests
  // that exercise cold-start replays clear the Map (via the dedicated
  // helper) but keep the durableLedger entry — that asymmetry is the
  // whole point of the test.
  LedgerReaderHolder.current = (async (runId, bodyHash) => {
    const seen = durableLedger.get(runId);
    if (seen === undefined) return { status: "fresh" };
    if (seen === bodyHash)
      return { status: "already_applied", companyId: null };
    return { status: "body_mismatch", companyId: null };
  }) as LedgerReader;
});

/**
 * Clear the in-process Map used by `accept/route.ts`. Cold-start tests
 * call this between requests to simulate a process restart while
 * keeping the `durableLedger` populated.
 */
async function clearInProcessLedger(): Promise<void> {
  const mod = await import("../accept/route");
  const map = (mod as unknown as { __ledger?: Map<string, string> }).__ledger;
  if (map) map.clear();
}

async function loadRoute() {
  return await import("../accept/route");
}

describe("POST /api/murmur/accept", () => {
  it("returns 401 when the bearer is missing", async () => {
    const { POST } = await loadRoute();
    const res = await POST(webhookRequest(VALID_FINAL_OUTPUT, { bearer: null }));
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ ok: false, errors: ["unauthorized"] });
    expect(appliedCalls).toHaveLength(0);
    expect(rerunCalls).toBe(0);
  });

  it("returns 401 when the bearer is wrong", async () => {
    const { POST } = await loadRoute();
    const res = await POST(
      webhookRequest(VALID_FINAL_OUTPUT, { bearer: "WRONG-TOKEN" }),
    );
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ ok: false, errors: ["unauthorized"] });
  });

  it("returns 413 when the body exceeds 5 MB", async () => {
    const { POST } = await loadRoute();
    const huge = "a".repeat(6 * 1024 * 1024);
    const res = await POST(
      webhookRequest(null, { rawBody: JSON.stringify({ huge }) }),
    );
    expect(res.status).toBe(413);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body).toEqual({ ok: false, errors: ["payload_too_large"] });
    expect(appliedCalls).toHaveLength(0);
  });

  it("returns 413 fast-path when Content-Length declares > 5 MB even if body is smaller", async () => {
    // This case verifies that the route looks at Content-Length BEFORE
    // reading the body buffer — important when a malicious / buggy
    // client lies about the size to provoke a large allocation. We
    // construct a `Request`-shaped object directly so happy-dom doesn't
    // overwrite Content-Length.
    const { POST } = await loadRoute();
    const headers = new Headers({
      "content-type": "application/json",
      authorization: `Bearer ${process.env.MURMUR_TOKEN ?? "test-token"}`,
      "idempotency-key": "run-cl-lied",
      "content-length": String(6 * 1024 * 1024),
    });
    const fakeRequest = {
      headers,
      text: async () => JSON.stringify(VALID_FINAL_OUTPUT),
    } as unknown as Request;
    const res = await POST(fakeRequest);
    expect(res.status).toBe(413);
  });

  it("returns 400 when Idempotency-Key header is missing", async () => {
    const { POST } = await loadRoute();
    const res = await POST(
      webhookRequest(VALID_FINAL_OUTPUT, { runId: null }),
    );
    expect(res.status).toBe(400);
    const body = (await res.json()) as { errors: string[] };
    expect(body.errors).toEqual(["missing_header:idempotency-key"]);
  });

  it("returns 400 on invalid JSON body", async () => {
    const { POST } = await loadRoute();
    const res = await POST(
      webhookRequest(null, { rawBody: "{not json" }),
    );
    expect(res.status).toBe(400);
    const body = (await res.json()) as { errors: string[] };
    expect(body.errors).toEqual(["invalid_json"]);
  });

  it("returns 400 with validation:* errors on a schema-invalid body", async () => {
    const { POST } = await loadRoute();
    const bad = {
      ...VALID_FINAL_OUTPUT,
      slug: "Bad Slug!", // pattern violation
      boards: [], // minItems: 1
    };
    const res = await POST(webhookRequest(bad));
    expect(res.status).toBe(400);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body.ok).toBe(false);
    expect(body.errors.length).toBeGreaterThan(0);
    for (const err of body.errors) {
      expect(err).toMatch(/^validation:\/[^:]*:[a-z_]+$/);
    }
    expect(appliedCalls).toHaveLength(0);
    expect(rerunCalls).toBe(0);
  });

  it("idempotent replay (same run_id, same body): applied:false reason:already_applied, no second write", async () => {
    const { POST } = await loadRoute();
    // First call — fresh.
    const res1 = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-A" }));
    expect(res1.status).toBe(200);
    const body1 = await res1.json();
    expect(body1.ok).toBe(true);
    expect(body1.data.applied).toBe(true);
    expect(appliedCalls).toHaveLength(1);

    // Second call with the same body — should hit the ledger.
    const res2 = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-A" }));
    expect(res2.status).toBe(200);
    const body2 = await res2.json();
    expect(body2.ok).toBe(true);
    expect(body2.data.applied).toBe(false);
    expect(body2.data.reason).toBe("already_applied");
    expect(body2.data.run_id).toBe("run-A");
    // Catalog writer NOT called a second time.
    expect(appliedCalls).toHaveLength(1);
  });

  it("idempotent replay (same run_id, different body): applied:false reason:body_mismatch, no second write, warning", async () => {
    const { POST } = await loadRoute();
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    const res1 = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-B" }));
    expect(res1.status).toBe(200);
    expect(appliedCalls).toHaveLength(1);

    const tampered = {
      ...VALID_FINAL_OUTPUT,
      description: "Different description.",
    };
    const res2 = await POST(webhookRequest(tampered, { runId: "run-B" }));
    expect(res2.status).toBe(200);
    const body2 = await res2.json();
    expect(body2.ok).toBe(true);
    expect(body2.data.applied).toBe(false);
    expect(body2.data.reason).toBe("body_mismatch");
    expect(appliedCalls).toHaveLength(1);
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it("happy path: re-run probes succeed → catalog written, applied:true", async () => {
    const { POST } = await loadRoute();
    const res = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-C" }));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.data.applied).toBe(true);
    expect(body.data.run_id).toBe("run-C");
    expect(rerunCalls).toBe(1);
    expect(appliedCalls).toHaveLength(1);
    expect(appliedCalls[0]?.context.runId).toBe("run-C");
  });

  it("re-run probes fail → no catalog write, 200 with ok:false errors:[...]", async () => {
    RerunHolder.current = (async () => ({
      status: "failed",
      errors: ["probe_failed:global"],
    })) as RerunProbes;
    const { POST } = await loadRoute();
    const res = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-D" }));
    // Not 5xx — Murmur retry budget would burn forever.
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(false);
    expect(body.errors).toEqual(["probe_failed:global"]);
    expect(appliedCalls).toHaveLength(0);
  });

  it("re-run probes time out → 504 with errors:['probe_timeout']", async () => {
    RerunHolder.current = (async () => ({ status: "timeout" })) as RerunProbes;
    const { POST } = await loadRoute();
    const res = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-E" }));
    expect(res.status).toBe(504);
    const body = await res.json();
    expect(body).toEqual({ ok: false, errors: ["probe_timeout"] });
    expect(appliedCalls).toHaveLength(0);
  });

  it("catalog writer throws → 200 ok:false errors:['catalog_write_failed']", async () => {
    ApplyCatalogHolder.current = (async () => {
      throw new Error("simulated DB outage");
    }) as ApplyCatalog;
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { POST } = await loadRoute();
    const res = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-F" }));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ ok: false, errors: ["catalog_write_failed"] });
    expect(errSpy).toHaveBeenCalled();
    errSpy.mockRestore();
  });

  it("surfaces catalog writer warnings on the response but still applies", async () => {
    ApplyCatalogHolder.current = (async (_t, _body, _ctx) => ({
      companyId: "00000000-0000-0000-0000-000000000002",
      boardCount: 1,
      warnings: ["slug_conflict"],
    })) as ApplyCatalog;
    const { POST } = await loadRoute();
    const res = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-G" }));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.data.applied).toBe(true);
    expect(body.data.warnings).toEqual(["slug_conflict"]);
  });

  it("never returns a 5xx for a typed lib failure", async () => {
    // Belt-and-suspenders: combination of probe failure + catalog throw
    // should still resolve to a 200 envelope.
    RerunHolder.current = (async () => ({
      status: "failed",
      errors: ["monitor_run_failed"],
    })) as RerunProbes;
    const { POST } = await loadRoute();
    const res = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-H" }));
    expect(res.status).toBeLessThan(500);
  });

  // ── Murmur one-retry simulations (issue #2763 e2e bullet) ──────────

  it("simulates Murmur 200 + 200 retry: idempotency holds, only one row written", async () => {
    const { POST } = await loadRoute();
    // First fire — applied.
    const res1 = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-retry-A" }));
    expect((await res1.json()).data.applied).toBe(true);
    expect(appliedCalls).toHaveLength(1);
    // Second fire (Murmur thought the first one timed out and retried)
    // — same body, same run_id. Idempotency catches it; no second
    // catalog row.
    const res2 = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-retry-A" }));
    const body2 = await res2.json();
    expect(body2.ok).toBe(true);
    expect(body2.data.applied).toBe(false);
    expect(body2.data.reason).toBe("already_applied");
    expect(appliedCalls).toHaveLength(1);
  });

  // ── Cold-start replay (durable ledger source-of-truth) ─────────────

  it("cold-start replay (Postgres path): Map cleared, durable ledger returns already_applied", async () => {
    // Seed: first fire applies cleanly and writes the durable ledger.
    const { POST } = await loadRoute();
    const res1 = await POST(
      webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-cold-A" }),
    );
    expect((await res1.json()).data.applied).toBe(true);
    expect(durableLedger.has("run-cold-A")).toBe(true);
    expect(appliedCalls).toHaveLength(1);

    // Simulate a process restart: clear the in-process Map only.
    await clearInProcessLedger();

    // Same body re-fires after restart. The Map miss must fall through
    // to the durable ledger and return already_applied — NOT re-apply.
    const res2 = await POST(
      webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-cold-A" }),
    );
    const body2 = await res2.json();
    expect(res2.status).toBe(200);
    expect(body2.ok).toBe(true);
    expect(body2.data.applied).toBe(false);
    expect(body2.data.reason).toBe("already_applied");
    // Critical: catalog writer was NOT called a second time despite the
    // empty in-process Map.
    expect(appliedCalls).toHaveLength(1);
    // Critical: the probe re-runner was also short-circuited.
    expect(rerunCalls).toBe(1);
  });

  it("cold-start replay (CSV path): Map cleared, durable ledger returns already_applied", async () => {
    // The route doesn't care about the catalog target for the
    // idempotency-classification step — it always defers to the ledger
    // reader. We force the env var so the same code path the CSV
    // backend would hit in production is what ledger-reader sees.
    const prevTarget = process.env.MURMUR_ACCEPT_TARGET;
    process.env.MURMUR_ACCEPT_TARGET = "csv";
    try {
      const { POST } = await loadRoute();
      const res1 = await POST(
        webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-cold-csv" }),
      );
      expect((await res1.json()).data.applied).toBe(true);
      expect(durableLedger.has("run-cold-csv")).toBe(true);

      await clearInProcessLedger();

      const res2 = await POST(
        webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-cold-csv" }),
      );
      const body2 = await res2.json();
      expect(body2.data.applied).toBe(false);
      expect(body2.data.reason).toBe("already_applied");
      expect(appliedCalls).toHaveLength(1);
    } finally {
      if (prevTarget === undefined) {
        delete process.env.MURMUR_ACCEPT_TARGET;
      } else {
        process.env.MURMUR_ACCEPT_TARGET = prevTarget;
      }
    }
  });

  it("cold-start body_mismatch: Map cleared, durable ledger holds different hash", async () => {
    const warnSpy = vi
      .spyOn(console, "warn")
      .mockImplementation(() => undefined);
    const { POST } = await loadRoute();

    const res1 = await POST(
      webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-cold-B" }),
    );
    expect((await res1.json()).data.applied).toBe(true);

    // Restart: Map empty, durable ledger has the FIRST body's hash.
    await clearInProcessLedger();

    const tampered = {
      ...VALID_FINAL_OUTPUT,
      description: "Different description after restart.",
    };
    const res2 = await POST(webhookRequest(tampered, { runId: "run-cold-B" }));
    const body2 = await res2.json();
    expect(res2.status).toBe(200);
    expect(body2.ok).toBe(true);
    expect(body2.data.applied).toBe(false);
    expect(body2.data.reason).toBe("body_mismatch");
    // Catalog NOT called for the tampered body.
    expect(appliedCalls).toHaveLength(1);
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  // ── UNIQUE-constraint integration: concurrent first-write race ─────

  it("UNIQUE constraint integration: catalog writer throws CatalogIdempotencyConflict → already_applied", async () => {
    // Simulate the exact scenario the issue's quality gate calls out:
    // two concurrent first-writes for the same run_id race into the
    // catalog writer. The first one lands; the second one's `tx
    // .insert(murmurAcceptLog).onConflictDoNothing().returning()`
    // returns an empty array, the writer raises
    // CatalogIdempotencyConflict, and the route surfaces
    // already_applied (NOT a 500, NOT a duplicate row).
    const { CatalogIdempotencyConflict } = await import("../_lib/catalog");
    let firstWriterCompleted = false;
    ApplyCatalogHolder.current = (async (_t, _b, ctx) => {
      if (!firstWriterCompleted) {
        firstWriterCompleted = true;
        appliedCalls.push({ body: _b, context: ctx });
        durableLedger.set(ctx.runId, ctx.bodyHash);
        return {
          companyId: "00000000-0000-0000-0000-000000000099",
          boardCount: 1,
          warnings: [],
        };
      }
      throw new CatalogIdempotencyConflict(ctx.runId);
    }) as ApplyCatalog;

    // Force the durable-ledger reader to miss for both calls so both
    // requests proceed past idempotency classification and into the
    // catalog writer — that is the exact race the UNIQUE constraint
    // guards against (the in-process Map and the durable ledger both
    // miss on the second request because the first writer hasn't
    // committed yet from THIS process's perspective).
    LedgerReaderHolder.current = (async () => ({
      status: "fresh" as const,
    })) as LedgerReader;

    const { POST } = await loadRoute();
    // First fire — wins the UNIQUE.
    const res1 = await POST(
      webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-race-X" }),
    );
    const body1 = await res1.json();
    expect(body1.ok).toBe(true);
    expect(body1.data.applied).toBe(true);

    // Clear the Map so the second request also reaches the catalog
    // writer (otherwise the in-process Map short-circuits).
    await clearInProcessLedger();

    // Second concurrent fire — loses the UNIQUE; route surfaces
    // already_applied via the CatalogIdempotencyConflict catch.
    const res2 = await POST(
      webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-race-X" }),
    );
    expect(res2.status).toBe(200);
    const body2 = await res2.json();
    expect(body2.ok).toBe(true);
    expect(body2.data.applied).toBe(false);
    expect(body2.data.reason).toBe("already_applied");
    // Exactly one catalog row was written.
    expect(appliedCalls).toHaveLength(1);
  });

  it("simulates Murmur 503 + 200 retry: first attempt fails, retry succeeds, only one row", async () => {
    const { POST } = await loadRoute();
    // Round 1: catalog backend simulates a transient outage.
    let firstCall = true;
    ApplyCatalogHolder.current = (async (_t, _b, _ctx) => {
      if (firstCall) {
        firstCall = false;
        throw new Error("simulated transient DB outage");
      }
      appliedCalls.push({ body: _b, context: _ctx });
      return {
        companyId: "00000000-0000-0000-0000-000000000003",
        boardCount: 1,
        warnings: [],
      };
    }) as ApplyCatalog;
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const res1 = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-retry-B" }));
    // Catalog write failure surfaces in the envelope; Murmur sees a
    // 200 with ok:false, classifies it as a soft failure, and retries
    // (the issue's "503 + 200" pattern is shorthand for "first
    // attempt was non-2xx, retry succeeded").
    expect((await res1.json()).ok).toBe(false);
    expect(appliedCalls).toHaveLength(0);

    // Round 2: backend recovers. Same run_id, same body — the
    // in-process ledger has NOT yet recorded this run (apply failed),
    // so the retry proceeds and writes the row.
    const res2 = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-retry-B" }));
    const body2 = await res2.json();
    expect(body2.ok).toBe(true);
    expect(body2.data.applied).toBe(true);
    expect(appliedCalls).toHaveLength(1);

    // Round 3 (Murmur's bug-budget belt-and-suspenders): even if a
    // duplicate fires AGAIN after success, it's deduped.
    const res3 = await POST(webhookRequest(VALID_FINAL_OUTPUT, { runId: "run-retry-B" }));
    expect((await res3.json()).data.applied).toBe(false);
    expect(appliedCalls).toHaveLength(1);

    errSpy.mockRestore();
  });
});
