/**
 * End-to-end test for the Murmur shim — exercises one route
 * (`POST /api/murmur/probes/monitor`) all the way through the real
 * `defaultInvoker`, which forks the Python `cli_shim` subprocess and
 * runs `probe_monitor` against a fixture board.
 *
 * Why this test is gated:
 *
 *   - The default invoker spawns Python and pulls in Playwright +
 *     httpx, so it's slow (~5–15 s) and depends on a working Python
 *     env in `apps/crawler` (asyncpg, playwright, httpx, etc.).
 *   - It also reaches the public network to probe a real greenhouse
 *     board; we don't want CI flakes when greenhouse.io is rate-limiting
 *     or returning HTML changes.
 *
 * Therefore the test is **opt-in** via `RUN_E2E_MURMUR=1`. The
 * orchestrator runs it locally before merge; CI without the flag skips.
 *
 * Required local environment when running:
 *   RUN_E2E_MURMUR=1                  (gate)
 *   MURMUR_TOKEN=<anything>           (route auth — defaults to test-token here)
 *   MURMUR_PY=<path/to/python>        (optional; defaults to `python3`. Use the
 *                                      crawler venv: `apps/crawler/.venv/bin/python`)
 *   MURMUR_CRAWLER_ROOT=<...>         (optional; defaults to ../crawler relative
 *                                      to apps/web)
 *   MURMUR_INVOKE_TIMEOUT_MS=<...>    (optional but recommended: a real probe
 *                                      against greenhouse takes ~30 s, which
 *                                      exceeds the 30 s production default.
 *                                      Set to 120000 locally.)
 *   DATABASE_URL=<...>                (optional; only required for routes that
 *                                      touch PostgresClaimKV — `probes/monitor`
 *                                      does not, so the test runs without a DB)
 *
 * Reproduce locally:
 *
 *   cd apps/web
 *   MURMUR_INVOKE_TIMEOUT_MS=180000 \
 *     MURMUR_PY=$(pwd)/../crawler/.venv/bin/python \
 *     MURMUR_CRAWLER_ROOT=$(pwd)/../crawler \
 *     pnpm test:e2e:murmur
 *
 * The SSRF module is still mocked here: J4 is exercised exhaustively in
 * `routes.test.ts`. This test cares about the lib boundary, not the
 * allowlist, so we let `validateUrl` pass through.
 *
 * @see colophon-group/jobseek#2759
 */
import { describe, it, expect } from "vitest";

import "./_helpers";
import { authedRequest, GREENHOUSE_URL } from "./_helpers";
import { InvokerHolder, defaultInvoker } from "../_lib/invoke-lib";

const E2E_ENABLED = process.env.RUN_E2E_MURMUR === "1";

interface ProbeMonitorData {
  board_url: string;
  current_jobs: number;
  entries: Array<{ name: string; metadata?: unknown; comment?: string }>;
  scored: Array<{ name: string }>;
}

interface OkEnvelope {
  ok: true;
  data: ProbeMonitorData;
}

interface ErrEnvelope {
  ok: false;
  errors: string[];
}

type Envelope = OkEnvelope | ErrEnvelope;

describe.skipIf(!E2E_ENABLED)("e2e: POST /api/murmur/probes/monitor", () => {
  it(
    "returns a probe-monitor envelope with the expected shape",
    async () => {
      // Use the real invoker. `_helpers.ts`'s `beforeEach` does NOT
      // touch InvokerHolder.current, so this assignment sticks for the
      // duration of the test.
      InvokerHolder.current = defaultInvoker;

      // Bump the invoker's wallclock cap unless the operator already
      // overrode it: a real greenhouse probe runs ~30 s, which is right
      // at the production default and trips the timeout intermittently.
      if (!process.env.MURMUR_INVOKE_TIMEOUT_MS) {
        process.env.MURMUR_INVOKE_TIMEOUT_MS = "180000";
      }

      // Allow the SSRF mock to pass the greenhouse URL through (default
      // behaviour in _helpers.ts is allow-by-default for parseable URLs).
      globalThis.__ssrfDecision = null;

      // The route still requires a valid bearer; _helpers seeds
      // process.env.MURMUR_TOKEN to "test-token" in beforeEach.
      const mod = await import("../probes/monitor/route");
      const url = `https://test.local/api/murmur/probes/monitor`;
      const req = authedRequest(url, {
        board_url: GREENHOUSE_URL,
        expected_count: 0,
      });

      const res = await mod.POST(req);
      expect(res.status).toBe(200);
      const body = (await res.json()) as Envelope;

      // The envelope shape is the contract; the precise probe results
      // (which monitors detected greenhouse, exact entry count) are
      // owned by `apps/crawler`'s probe registry and must not be locked
      // down here. We verify only structural invariants that a working
      // probe must satisfy.
      if (!body.ok) {
        // Surface the errors loudly so a flaky network shows up clearly
        // in the test output instead of failing on a missing field.
        throw new Error(
          `e2e probe_monitor returned an error envelope: ${JSON.stringify(body.errors)}`,
        );
      }

      expect(body.ok).toBe(true);
      expect(body.data).toBeDefined();
      expect(body.data.board_url).toBe(GREENHOUSE_URL);
      expect(typeof body.data.current_jobs).toBe("number");
      expect(Array.isArray(body.data.entries)).toBe(true);
      expect(Array.isArray(body.data.scored)).toBe(true);
      // A real greenhouse probe has at least one entry per registered
      // monitor (some not-detected, some detected). We don't pin the
      // count, only that the registry returned something.
      expect(body.data.entries.length).toBeGreaterThan(0);
      // Every entry has a name (string).
      for (const entry of body.data.entries) {
        expect(typeof entry.name).toBe("string");
      }
    },
    // Generous timeout: subprocess spawn (~500 ms) + Playwright/httpx
    // probe of a public board (5–15 s) + safety margin.
    60_000,
  );
});
