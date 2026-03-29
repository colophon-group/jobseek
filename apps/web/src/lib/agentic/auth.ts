import { SignJWT, jwtVerify } from "jose";
import { timingSafeEqual } from "crypto";

export const SESSION_COOKIE = "admin_session";
const TTL_SECONDS = 60 * 60 * 24 * 7; // 7 days

function getSecret(): Uint8Array {
  const secret = process.env.ADMIN_JWT_SECRET;
  if (!secret) throw new Error("ADMIN_JWT_SECRET is not set");
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
    const a = Buffer.from(submitted.padEnd(64));
    const b = Buffer.from(expected.padEnd(64));
    return (
      submitted.length === expected.length &&
      timingSafeEqual(a.subarray(0, 64), b.subarray(0, 64))
    );
  } catch {
    return false;
  }
}

export const SESSION_TTL_SECONDS = TTL_SECONDS;
