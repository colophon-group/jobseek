import "server-only";

import { timingSafeEqual } from "node:crypto";

export function matchesBasicAuthorization(
  authorization: string | null,
  expectedToken: string | undefined,
): boolean {
  if (!authorization || !expectedToken) return false;
  const [scheme, token] = authorization.split(" ", 2);
  // Scheme is non-secret (the literal string "Basic"); plain `===` is fine.
  if (scheme !== "Basic" || !token) return false;
  // Constant-time compare on the secret token. Mirrors the
  // `_safeBearerEqual` pattern in
  // `apps/web/app/api/internal/invalidate-typeahead/route.ts`. The
  // length pre-check is itself a timing oracle, but only leaks the
  // expected-token length (a fixed per-env constant), not any bytes
  // of the secret. See colophon-group/jobseek#3225.
  const a = Buffer.from(token, "utf8");
  const b = Buffer.from(expectedToken, "utf8");
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}
