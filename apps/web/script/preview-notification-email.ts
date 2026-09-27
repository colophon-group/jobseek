/** Offline render only: no database, provider key or recipient email needed. */
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { renderNotificationEmail } from "../src/lib/notifications/render-email";
import type { NotificationDeliveryPlan } from "../src/lib/notifications/scheduler-core";

async function main() {
  const directory = resolve(process.argv[2] ?? "/tmp/jobseek-notification-review");
  await mkdir(directory, { recursive: true });
  const now = new Date("2026-09-27T10:00:00Z");
  const plan: NotificationDeliveryPlan = {
    deliveryId: "11111111-1111-4111-8111-111111111111", userId: "preview", cadence: "weekly", plannedAt: now,
    scheduledFor: now, windowStart: new Date("2026-09-20T10:00:00Z"), windowEnd: now,
    idempotencyKey: "preview-only", totalMatches: 3, watchlistMatchCount: 4, sourceResultsTruncated: false,
    displayPostings: [
      { title: "Senior Software Engineer", name: "Example Research", location: "Zurich, Switzerland" },
      { title: "Platform Engineer", name: "Example Systems", location: "Remote · Switzerland" },
      { title: "Frontend Engineer", name: "Example Studio", location: "Lausanne, Switzerland" },
    ].map((job, index) => ({ id: String(index), title: job.title, sourceUrl: "https://example.com/jobs", isActive: true,
      firstSeenAt: now.toISOString(), locationNames: [job.location], company: { id: String(index), name: job.name, slug: "example", icon: null },
      matchedWatchlists: [{ id: "11111111-1111-4111-8111-111111111111", label: "Engineering in Switzerland" }, ...(index === 1 ? [{ id: "22222222-2222-4222-8222-222222222222", label: "Remote roles" }] : [])],
    })),
  };
  for (const locale of ["en", "de", "fr", "it"]) {
    const email = renderNotificationEmail({ plan, locale, origin: "https://jseek.co", unsubscribeUrl: "https://jseek.co/api/notifications/unsubscribe?token=preview-only" });
    await writeFile(resolve(directory, `email-${locale}.html`), email.html);
    await writeFile(resolve(directory, `email-${locale}.txt`), email.text);
  }
  console.log(`Notification email previews: ${directory}`);
}
void main();
