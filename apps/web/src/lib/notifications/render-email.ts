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
function companyIconHtml(icon: string | null, name: string): string {
  const initials = Array.from(name.trim()).slice(0, 2).join("").toUpperCase() || "?";
  const fallback = `<span style="font-size:13px;font-weight:bold;color:#616661">${escapeEmailHtml(initials)}</span>`;
  if (!icon) return fallback;
  try {
    const url = new URL(icon);
    if (url.protocol !== "https:" || url.username || url.password) return fallback;
    return `<img src="${escapeEmailHtml(url.href)}" alt="${escapeEmailHtml(initials)}" width="32" height="32" style="display:block;width:32px;height:32px;object-fit:contain;border:0;border-radius:4px">`;
  } catch { return fallback; }
}

/** Same first-seen timestamp as watchlists; measured when the email is rendered. */
function postingAge(firstSeenAt: string, now: Date, locale: string): string | null {
  const timestamp = Date.parse(firstSeenAt);
  if (!Number.isFinite(timestamp)) return null;
  const seconds = Math.max(0, Math.floor((now.getTime() - timestamp) / 1000));
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "always" });
  for (const [unit, size] of [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]] as const) {
    if (seconds >= size) return format.format(-Math.floor(seconds / size), unit);
  }
  return new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(0, "second");
}

export function renderNotificationEmail(input: {
  plan: NotificationDeliveryPlan; locale: string; origin: string; unsubscribeUrl: string; now?: Date;
}): { subject: string; html: string; text: string } {
  const locale = notificationLocale(input.locale);
  const t = notificationCopy[locale];
  const e = escapeEmailHtml;
  const watchlistsUrl = `${input.origin}/${locale}/watchlists`;
  const settingsUrl = `${input.origin}/${locale}/settings#notifications`;
  const postings = input.plan.displayPostings.slice(0, 20);
  const now = input.now ?? new Date();
  const added = (firstSeenAt: string) => {
    const age = postingAge(firstSeenAt, now, locale);
    return age === null ? "" : t.added.replace("{age}", age);
  };
  const items = postings.map(p => {
    const labels = p.matchedWatchlists.map(w => `<a style="color:#23766a" href="${e(`${watchlistsUrl}/${encodeURIComponent(w.id)}`)}">${e(w.label)}</a>`).join(" · ");
    return `<tr><td style="padding:24px 0;border-bottom:1px solid #e5e7eb"><table role="presentation" cellpadding="0" cellspacing="0" style="margin-bottom:10px"><tr><td width="32" height="32" align="center" valign="middle" style="width:32px;height:32px;background:#f5f6f3;border-radius:4px">${companyIconHtml(p.company.icon, p.company.name)}</td><td style="padding-left:10px;color:#616661;font-size:14px;line-height:1.4">${e(p.company.name)}</td></tr></table><h2 style="margin:0 0 8px;font-size:19px;line-height:1.4"><a style="color:#162e26;text-decoration:none" href="${e(safeRoleUrl(p.sourceUrl, watchlistsUrl))}">${e(p.title || t.role)}</a></h2><p style="margin:0 0 10px;color:#616661;font-size:14px">${e([...(p.locationNames ?? []), added(p.firstSeenAt)].filter(Boolean).join(" · "))}</p><p style="margin:0;font-size:13px;color:#616661">${e(t.matches)}: ${labels}</p></td></tr>`;
  }).join("");
  const text = [t.heading, t.intro, ...postings.map(p => [
    `${p.title || t.role} — ${p.company.name}`, [...(p.locationNames ?? []), added(p.firstSeenAt)].filter(Boolean).join(" · "),
    safeRoleUrl(p.sourceUrl, watchlistsUrl),
    ...p.matchedWatchlists.map(w => `${w.label}: ${watchlistsUrl}/${encodeURIComponent(w.id)}`),
  ].join("\n")), t.limited, `${t.more}: ${watchlistsUrl}`, `${t.settings}: ${settingsUrl}`,
    `${t.unsubscribe}: ${input.unsubscribeUrl}`, t.footer].join("\n\n");
  const html = `<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;background:#f5f6f3;color:#162e26;font-family:Arial,Helvetica,sans-serif"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 16px"><table role="presentation" width="100%" style="max-width:600px;background:white;border-radius:16px" cellpadding="0" cellspacing="0"><tr><td style="padding:32px"><p style="margin:0 0 32px;font-size:18px;font-weight:bold">Job Seek<span style="color:#23766a">.</span></p><h1 style="font-size:30px;line-height:1.2;margin:0 0 16px">${e(t.heading)}</h1><p style="color:#616661;line-height:1.6">${e(t.intro)}</p><table role="presentation" width="100%" cellpadding="0" cellspacing="0">${items}</table><p style="font-size:13px;color:#616661;line-height:1.6">${e(t.limited)}</p><p style="margin:24px 0"><a style="display:inline-block;background:#162e26;color:white;border-radius:8px;padding:14px 20px;text-decoration:none" href="${e(watchlistsUrl)}">${e(t.more)}</a></p><p style="font-size:12px;color:#616661;line-height:1.6">${e(t.footer)}</p><p style="font-size:12px;line-height:1.8"><a style="color:#23766a" href="${e(settingsUrl)}">${e(t.settings)}</a><br><a style="color:#23766a" href="${e(input.unsubscribeUrl)}">${e(t.unsubscribe)}</a></p></td></tr></table></td></tr></table></body></html>`;
  return { subject: t.subject, html, text };
}
