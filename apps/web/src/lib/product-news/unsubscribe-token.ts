import "server-only";
import { createHmac, timingSafeEqual } from "node:crypto";
import { normalizeProductNewsEmail } from "./policy";

export function productNewsConsentId(token: string): string | null {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[A-Za-z0-9_-]{43}$/.test(token)
    ? token.split(".")[0]! : null;
}

export function createProductNewsUnsubscribeToken(id: string, email: string, secret: string): string {
  if (secret.length < 32) throw new Error("Product news unsubscribe secret is too short");
  const signature = createHmac("sha256", secret)
    .update(`jobseek-product-news-unsubscribe-v1\n${id}\n${normalizeProductNewsEmail(email)}`)
    .digest("base64url");
  return `${id}.${signature}`;
}

export function verifyProductNewsUnsubscribeToken(token: string, email: string, secret: string): boolean {
  const id = productNewsConsentId(token);
  if (!id || secret.length < 32) return false;
  const expected = Buffer.from(createProductNewsUnsubscribeToken(id, email, secret));
  const actual = Buffer.from(token);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}
