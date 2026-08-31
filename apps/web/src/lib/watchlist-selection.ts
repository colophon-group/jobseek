import "server-only";

import {
  createCipheriv,
  createDecipheriv,
  createHash,
  createHmac,
  randomBytes,
  timingSafeEqual,
} from "node:crypto";

export const WATCHLIST_SELECTION_COOKIE = "jobseek.watchlist-selection";
export const WATCHLIST_SELECTION_VERSION = "v2";
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
  nonce: string,
  ciphertext: string,
  secret: string,
): string {
  return createHmac("sha256", secret)
    .update(`${WATCHLIST_SELECTION_VERSION}:${userId}:${nonce}:${ciphertext}`)
    .digest("base64url");
}

function encryptionKey(secret: string): Buffer {
  return createHash("sha256")
    .update(`jobseek:watchlist-selection:${secret}`)
    .digest();
}

/**
 * Authenticated encryption keeps the selected UUID opaque on the wire. The
 * separately verified HMAC binds the versioned token to one session user.
 * Decryption is still only a hint: every consumer must resolve `(id, user)` in
 * Postgres before returning data or accepting a selection.
 */
export function encodeWatchlistSelection(
  userId: string,
  watchlistId: string,
  secret = selectionSecret(),
): string {
  if (!isWatchlistId(watchlistId)) {
    throw new Error("Invalid watchlist id");
  }
  const nonce = randomBytes(12);
  const nonceValue = nonce.toString("base64url");
  const cipher = createCipheriv("aes-256-gcm", encryptionKey(secret), nonce);
  cipher.setAAD(Buffer.from(`${WATCHLIST_SELECTION_VERSION}:${userId}`));
  const encrypted = Buffer.concat([
    cipher.update(watchlistId, "utf8"),
    cipher.final(),
  ]);
  const ciphertext = Buffer.concat([encrypted, cipher.getAuthTag()])
    .toString("base64url");
  return [
    WATCHLIST_SELECTION_VERSION,
    nonceValue,
    ciphertext,
    signatureFor(userId, nonceValue, ciphertext, secret),
  ].join(".");
}

export function decodeWatchlistSelection(
  value: string | undefined,
  userId: string,
  secret = selectionSecret(),
): string | null {
  if (!value) return null;
  const [version, nonce, ciphertext, signature, extra] = value.split(".");
  if (
    version !== WATCHLIST_SELECTION_VERSION ||
    !nonce ||
    !ciphertext ||
    !signature ||
    extra !== undefined
  ) {
    return null;
  }

  const expected = signatureFor(userId, nonce, ciphertext, secret);
  const actualBuffer = Buffer.from(signature);
  const expectedBuffer = Buffer.from(expected);
  if (
    actualBuffer.length !== expectedBuffer.length ||
    !timingSafeEqual(actualBuffer, expectedBuffer)
  ) {
    return null;
  }
  try {
    const encrypted = Buffer.from(ciphertext, "base64url");
    if (encrypted.length <= 16) return null;
    const decipher = createDecipheriv(
      "aes-256-gcm",
      encryptionKey(secret),
      Buffer.from(nonce, "base64url"),
    );
    decipher.setAAD(Buffer.from(`${WATCHLIST_SELECTION_VERSION}:${userId}`));
    decipher.setAuthTag(encrypted.subarray(encrypted.length - 16));
    const watchlistId = Buffer.concat([
      decipher.update(encrypted.subarray(0, -16)),
      decipher.final(),
    ]).toString("utf8");
    return isWatchlistId(watchlistId) ? watchlistId : null;
  } catch {
    return null;
  }
}
