/**
 * Common request-handling shape for every Murmur shim route.
 *
 * Each route is a thin wrapper around `handleSubcommand` that pins:
 *   - the subcommand id (the lib dispatch key),
 *   - the input schema (vendored from the YAML),
 *   - the URL fields to validate via SSRF.
 *
 * The wrapper enforces the documented order:
 *   1. Bearer auth (FIRST — before any other I/O).
 *   2. Required Murmur headers.
 *   3. Body parse + schema validation.
 *   4. SSRF allowlist check (per-route URL fields).
 *   5. Lib invocation via `invokeLib`.
 *   6. Envelope return.
 *
 * Steps 3–6 produce envelope-shaped failures (HTTP 200 with
 * `{ ok: false, errors: [...] }`) per M0; only steps 1–2 produce
 * transport-level non-2xx (401, 400).
 *
 * @see colophon-group/jobseek#2759
 */

import { NextResponse } from "next/server";
import { validateUrl } from "@/lib/murmur/ssrf";
import { requireBearer } from "./auth";
import { requireMurmurHeaders } from "./headers-helper";
import { errJson, okJson } from "./envelope";
import { invokeLib, type LibSubcommand } from "./invoke-lib";
import { validateBody } from "./validate";
import type { SubcommandSchema } from "./schemas";

/**
 * Per-route configuration.
 */
export interface RouteSpec {
  /** Lib dispatch key (matches Python shim). */
  readonly libSubcommand: LibSubcommand;
  /** Vendored input schema. */
  readonly schema: SubcommandSchema;
  /**
   * Body fields that hold URLs the agent supplied. Each is run through
   * `validateUrl` (J4). Missing fields are skipped — required-ness is
   * already covered by the schema check before this step runs.
   */
  readonly urlFields: readonly string[];
}

/**
 * Single shared entry point for every route. Returns the HTTP response
 * the route must propagate verbatim.
 */
export async function handleSubcommand(
  request: Request,
  spec: RouteSpec,
): Promise<NextResponse> {
  // 1. Bearer auth — first line of work, no body read yet.
  const authFail = requireBearer(request);
  if (authFail) return authFail;

  // 2. Required Murmur headers.
  const headers = requireMurmurHeaders(request);
  if (!headers.ok) {
    return errJson(
      headers.missing.map((h) => `missing_header:${h}`),
      { status: 400 },
    );
  }

  // 3. Body parse + schema validation.
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return errJson(["invalid_json"], { status: 400 });
  }
  const schemaErrors = validateBody(body, spec.schema);
  if (schemaErrors.length > 0) {
    return NextResponse.json(
      {
        ok: false,
        errors: schemaErrors.map((e) => `schema:${e.path}:${e.message}`),
      },
      { status: 400 },
    );
  }

  // 4. SSRF allowlist on declared URL fields.
  for (const field of spec.urlFields) {
    const raw = (body as Record<string, unknown>)[field];
    if (typeof raw !== "string") continue;
    const v = await validateUrl(raw);
    if (!v.ok) {
      return errJson([v.error]);
    }
  }

  // 5. Lib invocation. Never throws on a typed lib failure — those are
  //    expressed in the envelope shape.
  let result;
  try {
    result = await invokeLib(spec.libSubcommand, body, headers.claim_token);
  } catch (err) {
    // Defensive: the invoker should not throw, but if it does we map
    // to internal_error rather than leaking a 500.
    // eslint-disable-next-line no-console
    console.error(
      `[murmur ${spec.libSubcommand}] invoker threw: ${(err as Error).message}`,
    );
    return errJson(["internal_error"]);
  }

  // 6. Envelope return — preserve whatever shape the lib returned.
  if (result.ok) {
    return okJson(result.data ?? null);
  }
  return errJson(result.errors ?? ["internal_error"]);
}
