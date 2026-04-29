/**
 * `task_tool` request/response envelope per Murmur DESIGN.md §4.2:
 *
 *     { ok: boolean, errors: string[]?, data: object? }
 *
 * Every Murmur shim route returns this shape — never a raw stack trace,
 * never an HTTP 5xx for a typed lib failure (those become
 * `{ ok: false, errors: [...] }` with a 200). HTTP non-2xx is reserved
 * for transport-level rejections (401 unauthorised, 400 missing /
 * malformed headers).
 *
 * @see colophon-group/jobseek#2759
 */

import { NextResponse } from "next/server";

/**
 * Successful envelope shape. `errors` is omitted on success.
 */
export interface OkEnvelope<T> {
  readonly ok: true;
  readonly data: T;
}

/**
 * Failure envelope shape. `errors` is a non-empty list of stable string
 * tokens — never user-facing prose, never a stack trace.
 */
export interface ErrEnvelope {
  readonly ok: false;
  readonly errors: readonly string[];
}

export type Envelope<T = unknown> = OkEnvelope<T> | ErrEnvelope;

/** Build an `{ ok: true, data }` 200 response. */
export function okJson<T>(data: T): NextResponse {
  const body: OkEnvelope<T> = { ok: true, data };
  return NextResponse.json(body, { status: 200 });
}

/**
 * Build an `{ ok: false, errors }` JSON response. The HTTP status
 * defaults to 200 (envelope-only failure) but callers may override
 * with 400 / 401 for transport-level rejections.
 */
export function errJson(
  errors: readonly string[],
  init?: { status?: number },
): NextResponse {
  const body: ErrEnvelope = { ok: false, errors };
  return NextResponse.json(body, { status: init?.status ?? 200 });
}
