import "server-only";

import { createHmac, timingSafeEqual } from "node:crypto";

export const WATCHLIST_SELECTION_COOKIE = "jobseek.watchlist-selection";
export const WATCHLIST_SELECTION_VERSION = "v1";
export const WATCHLIST_SELECTION_MAX_AGE = 60 * 60 * 24 * 90;

const WATCHLIST_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export const watchlistSelectionCookieOptions = {
  httpOnly: true,
  sameSite: "lax" as const,
  secure: process.env.NODE_ENV === "production",
  path: "/",
  maxAge: WATCHLIST_SELECTION_MAX_AGE,
};

export function isWatchlistId(value: string): boolean {
  return WATCHLIST_ID_PATTERN.test(value);
}

function selectionSecret(): string {
  const secret = process.env.BETTER_AUTH_SECRET;
  if (!secret) {
    throw new Error("BETTER_AUTH_SECRET is required for watchlist selection");
  }
  return secret;
}

function signatureFor(
  userId: string,
  watchlistId: string,
  secret: string,
): string {
  return createHmac("sha256", secret)
    .update(`${WATCHLIST_SELECTION_VERSION}:${userId}:${watchlistId}`)
    .digest("base64url");
}

/**
 * The persisted value exposes no username or mutable slug. Its UUID is only a
 * hint: every consumer must still resolve `(id, session user)` in Postgres.
 */
export function encodeWatchlistSelection(
  userId: string,
  watchlistId: string,
  secret = selectionSecret(),
): string {
  if (!isWatchlistId(watchlistId)) {
    throw new Error("Invalid watchlist id");
  }
  return [
    WATCHLIST_SELECTION_VERSION,
    watchlistId,
    signatureFor(userId, watchlistId, secret),
  ].join(".");
}

export function decodeWatchlistSelection(
  value: string | undefined,
  userId: string,
  secret = selectionSecret(),
): string | null {
  if (!value) return null;
  const [version, watchlistId, signature, extra] = value.split(".");
  if (
    version !== WATCHLIST_SELECTION_VERSION ||
    !isWatchlistId(watchlistId ?? "") ||
    !signature ||
    extra !== undefined
  ) {
    return null;
  }

  const expected = signatureFor(userId, watchlistId, secret);
  const actualBuffer = Buffer.from(signature);
  const expectedBuffer = Buffer.from(expected);
  if (
    actualBuffer.length !== expectedBuffer.length ||
    !timingSafeEqual(actualBuffer, expectedBuffer)
  ) {
    return null;
  }
  return watchlistId;
}
