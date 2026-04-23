import { NextRequest, NextResponse } from "next/server";
import { eq, gte, and, sql } from "drizzle-orm";
import { db } from "@/db";
import { jobPosting, company } from "@/db/schema";
import { getJobAiSummary } from "@/lib/actions/enrich-job";

export const maxDuration = 60;

const BATCH_SIZE = 5;
const LOOKBACK_HOURS = 2;
const LIMIT = 50;

export async function GET(req: NextRequest) {
  const secret = process.env.CRON_SECRET;
  if (secret) {
    const auth = req.headers.get("authorization");
    if (auth !== `Bearer ${secret}`) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
  }

  const since = new Date(Date.now() - LOOKBACK_HOURS * 60 * 60 * 1000);

  const jobs = await db
    .select({
      id: jobPosting.id,
      title: sql<string | null>`${jobPosting.titles}[1]`,
      companyName: company.name,
      locales: jobPosting.locales,
    })
    .from(jobPosting)
    .innerJoin(company, eq(jobPosting.companyId, company.id))
    .where(and(gte(jobPosting.firstSeenAt, since), eq(jobPosting.isActive, true)))
    .limit(LIMIT);

  const r2Domain = process.env.R2_DOMAIN_URL?.replace(/\/$/, "");
  let processed = 0;
  let failed = 0;
  let skipped = 0;

  for (let i = 0; i < jobs.length; i += BATCH_SIZE) {
    const batch = jobs.slice(i, i + BATCH_SIZE);
    await Promise.allSettled(
      batch.map(async (job) => {
        if (!r2Domain || !job.title) {
          skipped++;
          return;
        }
        const locale = job.locales?.includes("en") ? "en" : (job.locales?.[0] ?? "en");
        const descUrl = `${r2Domain}/job/${job.id}/${locale}/latest.html`;
        try {
          const resp = await fetch(descUrl);
          if (!resp.ok) {
            skipped++;
            return;
          }
          const descriptionHtml = await resp.text();
          const summary = await getJobAiSummary({
            postingId: job.id,
            title: job.title,
            descriptionHtml,
            companyName: job.companyName,
          });
          summary ? processed++ : skipped++;
        } catch {
          failed++;
        }
      }),
    );
  }

  return NextResponse.json({ processed, failed, skipped, total: jobs.length });
}
