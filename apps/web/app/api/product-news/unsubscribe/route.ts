import { unsubscribeProductNews } from "@/lib/services/product-news";
import { productNewsUnsubscribeCopy } from "@/lib/product-news/unsubscribe-copy";
import { escapeEmailHtml as escapeHtml } from "@/lib/notifications/render-email";
import type { Locale } from "@/lib/i18n";

const headers = {
  "Content-Type": "text/html; charset=utf-8", "Cache-Control": "private, no-store",
  "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow",
  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
};

function page(locale: Locale, state: "confirm" | "done" | "invalid" | "changed", token = "") {
  const copy = productNewsUnsubscribeCopy[locale];
  const title = state === "confirm" ? copy.confirm : state === "done" ? copy.done : copy.settings;
  const body = state === "confirm" ? copy.explanation : state === "done" ? copy.doneBody : state === "changed" ? copy.changed : copy.invalid;
  return new Response(`<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title></head><body style="margin:0;background:#f5f6f3;color:#162e26;font:16px/1.6 Arial,sans-serif"><main style="max-width:520px;margin:10vh auto;padding:32px"><p style="font-weight:bold">Job Seek.</p><h1 style="line-height:1.2">${escapeHtml(title)}</h1><p>${escapeHtml(body)}</p>${state === "confirm" ? `<form method="post" action="/api/product-news/unsubscribe?token=${escapeHtml(encodeURIComponent(token))}"><input type="hidden" name="List-Unsubscribe" value="One-Click"><button style="border:0;border-radius:8px;padding:14px 20px;background:#162e26;color:white;font:inherit;cursor:pointer" type="submit">${escapeHtml(copy.unsubscribe)}</button></form>` : ""}<p><a style="color:#23766a" href="/${locale}/settings#product-news">${escapeHtml(copy.settings)}</a></p></main></body></html>`, { headers, status: state === "invalid" ? 400 : 200 });
}

async function respond(request: Request, revoke: boolean) {
  const token = new URL(request.url).searchParams.get("token") ?? "";
  try {
    const recipient = await unsubscribeProductNews(token, revoke);
    if (!recipient) return page("en", "invalid");
    return page(recipient.locale, recipient.superseded ? "changed" : revoke ? "done" : "confirm", token);
  } catch {
    return new Response("Please try again shortly.", { status: 503, headers });
  }
}

// GET renders confirmation only: scanners and previews cannot revoke consent.
export async function GET(request: Request) { return respond(request, false); }

// RFC 8058 one-click POST works without an account session or login.
export async function POST(request: Request) {
  if (Number(request.headers.get("content-length") ?? 0) > 1024) return new Response(null, { status: 413, headers });
  const body = await request.text();
  if (body.length > 1024 || new URLSearchParams(body).get("List-Unsubscribe") !== "One-Click") return page("en", "invalid");
  return respond(request, true);
}
