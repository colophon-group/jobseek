import { Resend, type WebhookEventPayload } from "resend";
import { reconcileNotificationWebhook } from "@/lib/services/notification-webhook";

export async function POST(request: Request) {
  const secret = process.env.RESEND_WEBHOOK_SECRET;
  if (!secret) return new Response(null, { status: 503 });
  if (Number(request.headers.get("content-length") ?? 0) > 65536) return new Response(null, { status: 413 });
  const payload = await request.text();
  if (payload.length > 65536) return new Response(null, { status: 413 });
  let event: WebhookEventPayload;
  try {
    event = new Resend(process.env.RESEND_API_KEY).webhooks.verify({ payload, webhookSecret: secret,
      headers: { id: request.headers.get("svix-id") ?? "", timestamp: request.headers.get("svix-timestamp") ?? "", signature: request.headers.get("svix-signature") ?? "" },
    });
  } catch { return new Response(null, { status: 400 }); }
  try { await reconcileNotificationWebhook(event); }
  catch { return new Response(null, { status: 503 }); } // Provider retries, without exposing payloads.
  return new Response(null, { status: 204 });
}
