import { getPaddle } from "@/lib/paddle/client";
import { applyPaddleEvent } from "@/lib/paddle/webhooks";
import { logExternalError } from "@/lib/safe-external-error";

export async function POST(request: Request) {
  const secret = process.env.PADDLE_WEBHOOK_SECRET;
  if (!secret) return Response.json({ error: "Webhook not configured" }, { status: 503 });
  const signature = request.headers.get("paddle-signature");
  if (!signature) return Response.json({ error: "Missing signature" }, { status: 400 });
  let paddle;
  try {
    paddle = getPaddle();
  } catch {
    return Response.json({ error: "Webhook not configured" }, { status: 503 });
  }
  const body = await request.text();
  let event;
  try {
    event = await paddle.webhooks.unmarshal(body, secret, signature);
  } catch {
    return Response.json({ error: "Invalid webhook" }, { status: 400 });
  }
  try {
    await applyPaddleEvent(event);
    return Response.json({ received: true });
  } catch (error) {
    logExternalError("error", { service: "external_http", operation: "paddle.webhook" }, error);
    return Response.json({ error: "Webhook processing failed" }, { status: 503 });
  }
}
