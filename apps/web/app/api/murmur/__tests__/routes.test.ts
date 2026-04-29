/**
 * Route-level unit tests for the seven Murmur shim handlers.
 *
 * Each route is exercised through the documented matrix from
 * jobseek#2759's Verification block:
 *
 *   - bearer header missing               → 401
 *   - bearer header wrong                 → 401
 *   - bearer ok + missing claim-token     → 400
 *   - body schema-invalid                 → 400 with per-field path errors
 *   - URL fields rejected by SSRF         → { ok: false, errors: ["url_not_allowed"] }
 *   - happy path with stub lib            → { ok: true, data }
 *   - lib throws typed exception → mapped → { ok: false, errors: [...] } (no 5xx)
 *   - bearer-auth runs as the FIRST middleware (verified via lib-stub spy)
 *   - no route returns a raw stack trace on error
 *
 * The lib invoker is replaced per test via `InvokerHolder.current`. The
 * SSRF module is mocked in `_helpers.ts`.
 *
 * @see colophon-group/jobseek#2759
 */

import { describe, it, expect, beforeEach, vi } from "vitest";

import "./_helpers";
import {
  authedRequest,
  GREENHOUSE_URL,
  LEVER_URL,
} from "./_helpers";
import {
  InvokerHolder,
  type LibInvoker,
} from "../_lib/invoke-lib";

// Lazy-load route modules so mocks register first.
async function loadRoute(name: string) {
  switch (name) {
    case "probes/monitor":
      return await import("../probes/monitor/route");
    case "probes/scraper":
      return await import("../probes/scraper/route");
    case "run/monitor":
      return await import("../run/monitor/route");
    case "run/scraper":
      return await import("../run/scraper/route");
    case "select/monitor":
      return await import("../select/monitor/route");
    case "select/scraper":
      return await import("../select/scraper/route");
    case "feedback":
      return await import("../feedback/route");
    default:
      throw new Error(`unknown route: ${name}`);
  }
}

interface RouteCase {
  /** URL path under /api/murmur/. */
  readonly path: string;
  /** Expected `LibSubcommand` the route invokes. */
  readonly libSubcommand: string;
  /** A schema-valid body to use for happy-path / error mapping cases. */
  readonly validBody: Record<string, unknown>;
  /** Body that violates the route's schema (used for the 400 case). */
  readonly invalidBody: Record<string, unknown>;
  /**
   * Optional URL field within the body. When present, this enables the
   * SSRF rejection test. Routes with no URL fields skip that case.
   */
  readonly urlField?: string;
}

const ROUTE_CASES: readonly RouteCase[] = [
  {
    path: "probes/monitor",
    libSubcommand: "probe_monitor",
    validBody: { board_url: GREENHOUSE_URL, expected_count: 12 },
    invalidBody: { expected_count: -1 }, // missing board_url + below_minimum
    urlField: "board_url",
  },
  {
    path: "probes/scraper",
    libSubcommand: "probe_scraper",
    validBody: {
      board_url: GREENHOUSE_URL,
      monitor_type: "greenhouse",
      monitor_config: { foo: 1 },
    },
    invalidBody: {
      board_url: GREENHOUSE_URL,
      monitor_type: "",
      // monitor_config missing
    },
    urlField: "board_url",
  },
  {
    path: "run/monitor",
    libSubcommand: "run_monitor",
    validBody: { board_url: GREENHOUSE_URL },
    invalidBody: { board_url: "not-a-url" },
    urlField: "board_url",
  },
  {
    path: "run/scraper",
    libSubcommand: "run_scraper",
    validBody: { board_url: GREENHOUSE_URL, sample_job_url: LEVER_URL },
    invalidBody: { board_url: 123 },
    urlField: "board_url",
  },
  {
    path: "select/monitor",
    libSubcommand: "select_monitor",
    validBody: { candidate_id: "cfg-1", board_url: GREENHOUSE_URL },
    invalidBody: { candidate_id: "", board_url: GREENHOUSE_URL },
    urlField: "board_url",
  },
  {
    path: "select/scraper",
    libSubcommand: "select_scraper",
    validBody: { candidate_id: "cfg-1", board_url: GREENHOUSE_URL },
    invalidBody: { board_url: GREENHOUSE_URL }, // candidate_id missing
    urlField: "board_url",
  },
  {
    path: "feedback",
    libSubcommand: "feedback",
    validBody: { verdict: "ok", notes: "looks fine" },
    invalidBody: { verdict: "maybe" }, // not in enum
    // No URL field on /feedback per the YAML schema.
  },
];

beforeEach(() => {
  // Default to a passthrough invoker. Each test overrides as needed.
  InvokerHolder.current = (async () => ({
    ok: true,
    data: { default: true },
  })) as LibInvoker;
});

describe.each(ROUTE_CASES)("$path", (rc) => {
  const url = `https://test.local/api/murmur/${rc.path}`;

  it("returns 401 when the Authorization header is missing", async () => {
    const mod = await loadRoute(rc.path);
    const spy = vi.fn();
    InvokerHolder.current = spy as unknown as LibInvoker;

    const res = await mod.POST(
      authedRequest(url, rc.validBody, { skipBearer: true }),
    );
    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body).toEqual({ ok: false, errors: ["unauthorized"] });
    expect(spy).not.toHaveBeenCalled(); // bearer is FIRST
  });

  it("returns 401 when the Authorization header is wrong", async () => {
    const mod = await loadRoute(rc.path);
    const spy = vi.fn();
    InvokerHolder.current = spy as unknown as LibInvoker;

    const res = await mod.POST(
      authedRequest(url, rc.validBody, { bearer: "WRONG-TOKEN" }),
    );
    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body).toEqual({ ok: false, errors: ["unauthorized"] });
    expect(spy).not.toHaveBeenCalled();
  });

  it("returns 400 when X-Murmur-Claim-Token is missing", async () => {
    const mod = await loadRoute(rc.path);
    const spy = vi.fn();
    InvokerHolder.current = spy as unknown as LibInvoker;

    const res = await mod.POST(
      authedRequest(url, rc.validBody, { claimToken: null }),
    );
    expect(res.status).toBe(400);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body.ok).toBe(false);
    expect(body.errors).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/^missing_header:x-murmur-claim-token$/),
      ]),
    );
    expect(spy).not.toHaveBeenCalled();
  });

  it("returns 400 when X-Murmur-Subcommand is missing", async () => {
    const mod = await loadRoute(rc.path);
    const res = await mod.POST(
      authedRequest(url, rc.validBody, { subcommand: null }),
    );
    expect(res.status).toBe(400);
    const body = (await res.json()) as { errors: string[] };
    expect(body.errors).toEqual(
      expect.arrayContaining(["missing_header:x-murmur-subcommand"]),
    );
  });

  it("returns 400 with per-field schema errors on invalid body", async () => {
    const mod = await loadRoute(rc.path);
    const spy = vi.fn();
    InvokerHolder.current = spy as unknown as LibInvoker;

    const res = await mod.POST(authedRequest(url, rc.invalidBody));
    expect(res.status).toBe(400);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body.ok).toBe(false);
    // Every error has the per-field shape `schema:/<path>:<token>`.
    for (const err of body.errors) {
      expect(err).toMatch(/^schema:\/[^:]*:[a-z_]+$/);
    }
    expect(spy).not.toHaveBeenCalled();
  });

  it("returns 400 on invalid JSON body", async () => {
    const mod = await loadRoute(rc.path);
    const headers = new Headers({
      "content-type": "application/json",
      authorization: `Bearer ${process.env.MURMUR_TOKEN ?? "test-token"}`,
      "x-murmur-claim-token": "claim-abc",
      "x-murmur-subcommand": "probe monitor",
    });
    const req = new Request(url, {
      method: "POST",
      headers,
      body: "{not json",
    });
    const res = await mod.POST(req);
    expect(res.status).toBe(400);
    const body = (await res.json()) as { errors: string[] };
    expect(body.errors).toEqual(["invalid_json"]);
  });

  if (rc.urlField) {
    it("rejects URL fields that fail the SSRF allowlist", async () => {
      globalThis.__ssrfDecision = () => ({
        ok: false,
        error: "url_not_allowed",
      });
      const spy = vi.fn();
      InvokerHolder.current = spy as unknown as LibInvoker;
      const mod = await loadRoute(rc.path);

      const res = await mod.POST(authedRequest(url, rc.validBody));
      expect(res.status).toBe(200);
      const body = (await res.json()) as { ok: boolean; errors: string[] };
      expect(body).toEqual({ ok: false, errors: ["url_not_allowed"] });
      expect(spy).not.toHaveBeenCalled(); // SSRF before lib
    });
  }

  it("returns { ok: true, data } on the happy path", async () => {
    const dataPayload = { sentinel: rc.libSubcommand };
    let invokedWith: {
      sub: string;
      body: unknown;
      claim: string;
    } | null = null;
    InvokerHolder.current = async (sub, body, claim) => {
      invokedWith = { sub, body, claim };
      return { ok: true, data: dataPayload };
    };
    const mod = await loadRoute(rc.path);

    const res = await mod.POST(authedRequest(url, rc.validBody));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ ok: true, data: dataPayload });
    expect(invokedWith?.sub).toBe(rc.libSubcommand);
    expect(invokedWith?.claim).toBe("claim-abc");
    expect(invokedWith?.body).toEqual(rc.validBody);
  });

  it("maps a typed lib failure envelope to the response without leaking 5xx", async () => {
    InvokerHolder.current = async () => ({
      ok: false,
      errors: ["probe_failed"],
    });
    const mod = await loadRoute(rc.path);

    const res = await mod.POST(authedRequest(url, rc.validBody));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ ok: false, errors: ["probe_failed"] });
  });

  it("never leaks a stack trace when the invoker throws unexpectedly", async () => {
    InvokerHolder.current = async () => {
      throw new Error("simulated\n    at /private/path/cli_shim.py:42");
    };
    const mod = await loadRoute(rc.path);

    const res = await mod.POST(authedRequest(url, rc.validBody));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { ok: boolean; errors: string[] };
    expect(body).toEqual({ ok: false, errors: ["internal_error"] });
    const raw = JSON.stringify(body);
    expect(raw).not.toMatch(/at\s+\/private/);
    expect(raw).not.toMatch(/cli_shim\.py/);
  });
});
