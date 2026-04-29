/**
 * Shared test helpers for the Murmur shim route suites.
 *
 * Mocks the SSRF module + the lib invoker so unit tests run with no
 * network and no Python subprocesses. Each test suite imports this
 * module BEFORE importing the route it exercises so the `vi.mock`
 * calls register first.
 *
 * @see colophon-group/jobseek#2759
 */
import { vi, beforeEach } from "vitest";

// ── Mocks ──────────────────────────────────────────────────────────

// SSRF mock — tests push allow/deny decisions per host pattern.
vi.mock("@/lib/murmur/ssrf", () => {
  const validateUrl = vi.fn(async (input: string) => {
    if (typeof globalThis.__ssrfDecision === "function") {
      return globalThis.__ssrfDecision(input);
    }
    // Default: pass-through if URL is parseable.
    try {
      const u = new URL(input);
      return {
        ok: true as const,
        url: u,
        resolvedIp: "203.0.113.10",
        family: 4 as const,
      };
    } catch {
      return { ok: false as const, error: "url_invalid" as const };
    }
  });
  return { validateUrl };
});

// Lib invoker mock — tests overwrite `InvokerHolder.current` per case.
// We don't mock the module: `InvokerHolder` is an object with a mutable
// `current` field, so tests just replace the function reference.

// ── Setup helpers ──────────────────────────────────────────────────

declare global {
  // SSRF decision override. Set to `null` / `undefined` for default.
  // eslint-disable-next-line no-var
  var __ssrfDecision:
    | ((url: string) =>
        | {
            ok: true;
            url: URL;
            resolvedIp: string;
            family: 4 | 6;
          }
        | { ok: false; error: string })
    | null
    | undefined;
}

beforeEach(() => {
  globalThis.__ssrfDecision = null;
  // Restore a known token for every case; individual cases mutate it.
  process.env.MURMUR_TOKEN = "test-token";
});

/** Build a fully-formed authorised request with the canonical headers. */
export function authedRequest(
  url: string,
  body: unknown,
  overrides?: {
    bearer?: string;
    claimToken?: string | null;
    subcommand?: string | null;
    skipBearer?: boolean;
  },
): Request {
  const headers = new Headers({ "content-type": "application/json" });
  if (!overrides?.skipBearer) {
    const tok = overrides?.bearer ?? process.env.MURMUR_TOKEN ?? "test-token";
    headers.set("authorization", `Bearer ${tok}`);
  }
  if (overrides?.claimToken !== null) {
    headers.set("x-murmur-claim-token", overrides?.claimToken ?? "claim-abc");
  }
  if (overrides?.subcommand !== null) {
    headers.set(
      "x-murmur-subcommand",
      overrides?.subcommand ?? "probe monitor",
    );
  }
  return new Request(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

/** Stable greenhouse URL that passes the SSRF mock by default. */
export const GREENHOUSE_URL = "https://job-boards.greenhouse.io/acme";
/** Stable lever URL. */
export const LEVER_URL = "https://jobs.lever.co/acme/post-1";
