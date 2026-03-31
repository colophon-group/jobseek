import "server-only";
import { timingSafeEqual } from "crypto";

export function matchesBasicAuthorization(
  authorization: string | null,
  expectedToken: string | undefined,
): boolean {
  if (!authorization || !expectedToken) return false;
  const [scheme, token] = authorization.split(" ", 2);
  if (scheme !== "Basic" || !token) return false;
  try {
    const a = Buffer.from(token.padEnd(128));
    const b = Buffer.from(expectedToken.padEnd(128));
    return (
      token.length === expectedToken.length &&
      timingSafeEqual(a.subarray(0, 128), b.subarray(0, 128))
    );
  } catch {
    return false;
  }
}
