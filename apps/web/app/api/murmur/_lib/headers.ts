/**
 * Canonical Murmur header names — single source of truth.
 *
 * Every Murmur subcommand HTTP shim route reads these headers; the casing
 * is locked by Murmur's M0 contract. Pulling them from one constant keeps
 * grep-auditability tight and prevents accidental drift between routes.
 *
 * Spec: Murmur DESIGN.md §4.2 (Boundary contracts), §3.6 (proxy headers).
 *
 * @see colophon-group/jobseek#2759
 */

/**
 * RFC 7235 / 9110 — bearer-token header on agent → publisher hops.
 *
 * Murmur sends the shared `MURMUR_TOKEN` here when proxying a subcommand
 * call from the agent to the publisher. Routes verify the token with
 * `crypto.timingSafeEqual` before doing anything else.
 */
export const HEADER_AUTHORIZATION = "authorization" as const;

/**
 * Identifies the agent's per-subtask claim. Used as the partition key
 * for the per-claim KV (named-config state). M0-locked casing.
 */
export const HEADER_CLAIM_TOKEN = "x-murmur-claim-token" as const;

/**
 * Identifies which subcommand Murmur is dispatching. The route also knows
 * which subcommand it is from its path; we still read the header
 * defensively so a misrouted call (proxy bug, copy-paste) is rejected.
 */
export const HEADER_SUBCOMMAND = "x-murmur-subcommand" as const;
