import "server-only";
import { EMAIL_FROM, EMAIL_REPLY_TO } from "@/lib/email-config";
import type { renderNotificationEmail } from "./render-email";

export type NotificationProviderResult =
  | { status: "sent"; messageId: string }
  | { status: "failed" | "unknown"; errorCode: string };

/** No transport retries: uncertain acceptance must await a signed webhook. */
export async function sendNotificationEmail(input: {
  to: string; deliveryId: string; attempt: number; idempotencyKey: string; unsubscribeUrl: string;
  email: ReturnType<typeof renderNotificationEmail>;
}): Promise<NotificationProviderResult> {
  try {
    const response = await fetch("https://api.resend.com/emails", {
      method: "POST",
      signal: AbortSignal.timeout(10_000),
      headers: { Authorization: `Bearer ${process.env.RESEND_API_KEY}`, "Content-Type": "application/json", "Idempotency-Key": input.idempotencyKey },
      body: JSON.stringify({
        from: EMAIL_FROM, reply_to: EMAIL_REPLY_TO, to: [input.to], ...input.email,
        headers: { "List-Unsubscribe": `<${input.unsubscribeUrl}>`, "List-Unsubscribe-Post": "List-Unsubscribe=One-Click" },
        tags: [{ name: "notification_delivery", value: input.deliveryId }, { name: "notification_attempt", value: String(input.attempt) }],
      }),
    });
    if (response.ok) {
      const data = await response.json() as { id?: unknown };
      if (typeof data.id === "string" && data.id.length > 0) return { status: "sent", messageId: data.id };
      return { status: "unknown", errorCode: "provider_missing_id" };
    }
    // Only definitive rejections are retryable. Never log provider bodies (PII).
    return {
      status: [400, 401, 403, 404, 422, 429].includes(response.status) ? "failed" : "unknown",
      errorCode: `provider_http_${response.status}`,
    };
  } catch {
    return { status: "unknown", errorCode: "provider_transport_unknown" };
  }
}
