/**
 * Defensive reader for the two Murmur proxy headers
 * (`X-Murmur-Claim-Token` + `X-Murmur-Subcommand`). Returns a structured
 * result so the caller can build a 400 response listing every missing
 * header in one shot.
 *
 * @see colophon-group/jobseek#2759
 */

import { HEADER_CLAIM_TOKEN, HEADER_SUBCOMMAND } from "./headers";

export type RequiredHeadersResult =
  | { ok: true; claim_token: string; subcommand: string }
  | { ok: false; missing: readonly string[] };

/**
 * Read the two M0 proxy headers. The header names are taken from the
 * `headers.ts` constants; this helper is the only place that decides
 * "missing or empty == fail".
 *
 * Empty-string values are treated as missing — a `claim_token: ""` is
 * never legitimate, and the per-claim KV's primary key would silently
 * accept it.
 */
export function requireMurmurHeaders(request: Request): RequiredHeadersResult {
  const claim_token = (request.headers.get(HEADER_CLAIM_TOKEN) ?? "").trim();
  const subcommand = (request.headers.get(HEADER_SUBCOMMAND) ?? "").trim();

  const missing: string[] = [];
  if (!claim_token) missing.push(HEADER_CLAIM_TOKEN);
  if (!subcommand) missing.push(HEADER_SUBCOMMAND);

  if (missing.length > 0) {
    return { ok: false, missing };
  }
  return { ok: true, claim_token, subcommand };
}
