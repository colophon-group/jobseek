import { SignJWT, jwtVerify } from "jose";
import { timingSafeEqual } from "crypto";

export const SESSION_COOKIE = "admin_session";
const TTL_SECONDS = 60 * 60 * 24 * 7; // 7 days

function getSecret(): Uint8Array {
  const secret = process.env.ADMIN_JWT_SECRET;
  if (!secret) throw new Error("ADMIN_JWT_SECRET is not set");
  if (secret.length < 32) {
    throw new Error(
      "ADMIN_JWT_SECRET must be at least 32 characters (256 bits) for HS256",
    );
  }
  return new TextEncoder().encode(secret);
}

export async function createSessionToken(): Promise<string> {
  return new SignJWT({ role: "admin" })
    .setProtectedHeader({ alg: "HS256" })
    .setExpirationTime(`${TTL_SECONDS}s`)
    .setIssuedAt()
    .sign(getSecret());
}

export async function verifySessionToken(token: string): Promise<boolean> {
  try {
    await jwtVerify(token, getSecret());
    return true;
  } catch {
    return false;
  }
}

export function checkPassword(submitted: string): boolean {
  const expected = process.env.ADMIN_PASSWORD;
  if (!expected) throw new Error("ADMIN_PASSWORD is not set");
  try {
    // Encode both passwords to bytes, then pad to the same fixed length so
    // timingSafeEqual can compare without leaking which is longer.  We use
    // 256 bytes (well above any realistic password) so no real content is
    // ever truncated.  The explicit length equality check ensures passwords
    // that share a common prefix but differ in length are rejected.
    const PAD = 256;
    const a = Buffer.alloc(PAD);
    const b = Buffer.alloc(PAD);
    Buffer.from(submitted, "utf8").copy(a);
    Buffer.from(expected, "utf8").copy(b);
    return (
      submitted.length === expected.length && timingSafeEqual(a, b)
    );
  } catch {
    return false;
  }
}

export const SESSION_TTL_SECONDS = TTL_SECONDS;
