import { unsubscribeNotification } from "@/lib/services/notification-unsubscribe";
import { notificationCopy, notificationLocale } from "@/lib/notifications/email-copy";
import { escapeEmailHtml as e } from "@/lib/notifications/render-email";

const headers = {
  "Content-Type": "text/html; charset=utf-8", "Cache-Control": "private, no-store",
  "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow",
  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
};
function page(localeValue: string, state: "confirm" | "done" | "invalid", token = "") {
  const locale = notificationLocale(localeValue);
  const t = notificationCopy[locale];
  return new Response(`<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${e(state === "confirm" ? t.confirm : state === "done" ? t.done : t.settings)}</title></head><body style="margin:0;background:#f5f6f3;color:#162e26;font:16px/1.6 Arial,sans-serif"><main style="max-width:520px;margin:10vh auto;padding:32px"><p style="font-weight:bold">Job Seek.</p><h1 style="line-height:1.2">${e(state === "confirm" ? t.confirm : state === "done" ? t.done : t.settings)}</h1><p>${e(state === "confirm" ? t.explanation : state === "done" ? t.doneBody : t.invalid)}</p>${state === "confirm" ? `<form method="post" action="/api/notifications/unsubscribe?token=${e(encodeURIComponent(token))}"><input type="hidden" name="List-Unsubscribe" value="One-Click"><button style="border:0;border-radius:8px;padding:14px 20px;background:#162e26;color:white;font:inherit;cursor:pointer" type="submit">${e(t.unsubscribe)}</button></form>` : ""}<p><a style="color:#23766a" href="/${locale}/settings#notifications">${e(t.settings)}</a></p></main></body></html>`, { headers, status: state === "invalid" ? 400 : 200 });
}
export async function GET(request: Request) {
  const token = new URL(request.url).searchParams.get("token") ?? "";
  const recipient = await unsubscribeNotification(token, false);
  // Link scanners and email previews must never opt a user out through GET.
  return recipient ? page(recipient.locale, "confirm", token) : page("en", "invalid");
}
export async function POST(request: Request) {
  if (Number(request.headers.get("content-length") ?? 0) > 1024) return new Response(null, { status: 413, headers });
  const body = await request.text();
  if (body.length > 1024 || new URLSearchParams(body).get("List-Unsubscribe") !== "One-Click") return page("en", "invalid");
  const token = new URL(request.url).searchParams.get("token") ?? "";
  const recipient = await unsubscribeNotification(token, true);
  return recipient ? page(recipient.locale, "done") : page("en", "invalid");
}
