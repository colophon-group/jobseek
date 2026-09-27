import "server-only";
import { createHmac, timingSafeEqual } from "node:crypto";

export function createUnsubscribeToken(deliveryId: string, email: string, secret: string): string {
  if (secret.length < 32) throw new Error("Unsubscribe signing secret is too short");
  const signature = createHmac("sha256", secret)
    .update(`jobseek-notifications-unsubscribe-v1\n${deliveryId}\n${email.toLowerCase()}`)
    .digest("base64url");
  return `${deliveryId}.${signature}`;
}
export function unsubscribeDeliveryId(token: string): string | null {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[A-Za-z0-9_-]{43}$/.test(token)
    ? token.split(".")[0]! : null;
}
export function verifyUnsubscribeToken(token: string, email: string, secret: string): boolean {
  const id = unsubscribeDeliveryId(token);
  if (!id || secret.length < 32) return false;
  const expected = Buffer.from(createUnsubscribeToken(id, email, secret));
  const actual = Buffer.from(token);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}
