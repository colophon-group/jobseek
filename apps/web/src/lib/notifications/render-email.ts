import { notificationCopy, notificationLocale } from "./email-copy";
import type { NotificationDeliveryPlan } from "./scheduler-core";

export function escapeEmailHtml(value: string): string {
  return value.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
function safeRoleUrl(raw: string, fallback: string): string {
  try {
    const url = new URL(raw);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : fallback;
  } catch { return fallback; }
}
export function renderNotificationEmail(input: {
  plan: NotificationDeliveryPlan; locale: string; origin: string; unsubscribeUrl: string;
}): { subject: string; html: string; text: string } {
  const locale = notificationLocale(input.locale);
  const t = notificationCopy[locale];
  const e = escapeEmailHtml;
  const watchlistsUrl = `${input.origin}/${locale}/watchlists`;
  const settingsUrl = `${input.origin}/${locale}/settings#notifications`;
  const postings = input.plan.displayPostings.slice(0, 20);
  const items = postings.map(p => {
    const labels = p.matchedWatchlists.map(w => `<a style="color:#23766a" href="${e(`${watchlistsUrl}/${encodeURIComponent(w.id)}`)}">${e(w.label)}</a>`).join(" · ");
    return `<tr><td style="padding:24px 0;border-bottom:1px solid #e5e7eb"><p style="margin:0 0 6px;color:#616661;font-size:14px">${e(p.company.name)}</p><h2 style="margin:0 0 8px;font-size:19px;line-height:1.4"><a style="color:#162e26;text-decoration:none" href="${e(safeRoleUrl(p.sourceUrl, watchlistsUrl))}">${e(p.title || t.role)}</a></h2><p style="margin:0 0 10px;color:#616661;font-size:14px">${e((p.locationNames ?? []).join(" · "))}</p><p style="margin:0;font-size:13px;color:#616661">${e(t.matches)}: ${labels}</p></td></tr>`;
  }).join("");
  const text = [t.heading, t.intro, ...postings.map(p => [
    `${p.title || t.role} — ${p.company.name}`, (p.locationNames ?? []).join(" · "),
    safeRoleUrl(p.sourceUrl, watchlistsUrl),
    ...p.matchedWatchlists.map(w => `${w.label}: ${watchlistsUrl}/${encodeURIComponent(w.id)}`),
  ].join("\n")), t.limited, `${t.more}: ${watchlistsUrl}`, `${t.settings}: ${settingsUrl}`,
    `${t.unsubscribe}: ${input.unsubscribeUrl}`, t.footer].join("\n\n");
  const html = `<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;background:#f5f6f3;color:#162e26;font-family:Arial,Helvetica,sans-serif"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 16px"><table role="presentation" width="100%" style="max-width:600px;background:white;border-radius:16px" cellpadding="0" cellspacing="0"><tr><td style="padding:32px"><p style="margin:0 0 32px;font-size:18px;font-weight:bold">Job Seek<span style="color:#23766a">.</span></p><h1 style="font-size:30px;line-height:1.2;margin:0 0 16px">${e(t.heading)}</h1><p style="color:#616661;line-height:1.6">${e(t.intro)}</p><table role="presentation" width="100%" cellpadding="0" cellspacing="0">${items}</table><p style="font-size:13px;color:#616661;line-height:1.6">${e(t.limited)}</p><p style="margin:24px 0"><a style="display:inline-block;background:#162e26;color:white;border-radius:8px;padding:14px 20px;text-decoration:none" href="${e(watchlistsUrl)}">${e(t.more)}</a></p><p style="font-size:12px;color:#616661;line-height:1.6">${e(t.footer)}</p><p style="font-size:12px;line-height:1.8"><a style="color:#23766a" href="${e(settingsUrl)}">${e(t.settings)}</a><br><a style="color:#23766a" href="${e(input.unsubscribeUrl)}">${e(t.unsubscribe)}</a></p></td></tr></table></td></tr></table></body></html>`;
  return { subject: t.subject, html, text };
}
