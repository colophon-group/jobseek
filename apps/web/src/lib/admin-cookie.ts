import { createHmac, timingSafeEqual } from "crypto";

const SECRET = process.env.BETTER_AUTH_SECRET!;
const MAX_AGE_MS = 8 * 60 * 60 * 1000; // 8 hours

/**
 * Create a signed admin 2FA cookie value.
 * Format: `userId:timestamp:hmac`
 */
export function signAdminCookie(userId: string): string {
  const ts = Date.now().toString();
  const hmac = createHmac("sha256", SECRET)
    .update(`${userId}:${ts}`)
    .digest("hex");
  return `${userId}:${ts}:${hmac}`;
}

/**
 * Verify the admin 2FA cookie is valid and not expired.
 */
export function verifyAdminCookie(token: string, expectedUserId: string): boolean {
  const parts = token.split(":");
  if (parts.length !== 3) return false;

  const [userId, ts, hmac] = parts;
  if (userId !== expectedUserId) return false;

  // Check expiry
  const timestamp = parseInt(ts, 10);
  if (isNaN(timestamp) || Date.now() - timestamp > MAX_AGE_MS) return false;

  // Verify HMAC
  const expected = createHmac("sha256", SECRET)
    .update(`${userId}:${ts}`)
    .digest("hex");

  try {
    return timingSafeEqual(Buffer.from(hmac, "hex"), Buffer.from(expected, "hex"));
  } catch {
    return false;
  }
}
