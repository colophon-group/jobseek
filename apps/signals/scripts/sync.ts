import "dotenv/config";
import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import { eq, and } from "drizzle-orm";
import { randomUUID } from "crypto";
import { fetchOutreachDataset, type ApifyOutreachRecord } from "../src/lib/apify";
import * as schema from "../src/db/schema";

const { company, hiringSignal, outreachDraft } = schema;

const DB_URL = process.env.DATABASE_URL;
if (!DB_URL) {
  console.error("DATABASE_URL is not set");
  process.exit(1);
}

const sql = postgres(DB_URL);
const db = drizzle(sql, { schema });

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80);
}

async function resolveCompanyId(companyName: string): Promise<string> {
  // Try to find by name (case-insensitive via exact match on normalized slug)
  const slug = slugify(companyName);
  const existing = await db
    .select({ id: company.id })
    .from(company)
    .where(eq(company.slug, slug))
    .limit(1);

  if (existing.length) return existing[0].id;

  // Insert a stub company row
  const newId = randomUUID();
  await db
    .insert(company)
    .values({
      id: newId,
      name: companyName,
      slug,
      extras: { synced_from_apify: true },
    })
    .onConflictDoNothing();

  // Re-fetch in case of race condition
  const refetch = await db
    .select({ id: company.id })
    .from(company)
    .where(eq(company.slug, slug))
    .limit(1);

  return refetch[0]?.id ?? newId;
}

async function syncRecord(record: ApifyOutreachRecord): Promise<"inserted" | "skipped"> {
  const companyId = await resolveCompanyId(record.signal_company);

  // Upsert hiring_signal by source_id
  const signalDate = new Date(record.signal_date);
  const existingSignal = await db
    .select({ id: hiringSignal.id })
    .from(hiringSignal)
    .where(eq(hiringSignal.sourceId, record.signal_id))
    .limit(1);

  let signalId: string;
  if (existingSignal.length) {
    signalId = existingSignal[0].id;
    await db
      .update(hiringSignal)
      .set({
        score: record.final_score,
        reasoning: record.scoring_reasoning,
        updatedAt: new Date(),
      })
      .where(eq(hiringSignal.id, signalId));
  } else {
    signalId = randomUUID();
    await db.insert(hiringSignal).values({
      id: signalId,
      companyId,
      signalType: record.signal_type,
      signalText: record.signal_text,
      signalDate,
      sourceId: record.signal_id,
      score: record.final_score,
      reasoning: record.scoring_reasoning,
      metadata: {
        company_name: record.signal_company,
        source_url: record.source_url ?? null,
      },
    });
  }

  // Upsert outreach_draft — never overwrite sent/archived status
  const email = record.contact?.email ?? null;
  const existingDraft = await db
    .select({ id: outreachDraft.id, status: outreachDraft.status })
    .from(outreachDraft)
    .where(eq(outreachDraft.signalId, signalId))
    .limit(1);

  if (existingDraft.length) {
    const current = existingDraft[0];
    if (current.status === "sent" || current.status === "archived") {
      return "skipped";
    }
    await db
      .update(outreachDraft)
      .set({
        subject: record.subject,
        body: record.body,
        contactName: record.contact?.name ?? "Unknown",
        contactTitle: record.contact?.title ?? null,
        contactEmail: email,
        updatedAt: new Date(),
      })
      .where(eq(outreachDraft.id, current.id));
  } else {
    await db.insert(outreachDraft).values({
      id: randomUUID(),
      signalId,
      contactName: record.contact?.name ?? "Unknown",
      contactTitle: record.contact?.title ?? null,
      contactEmail: email,
      subject: record.subject,
      body: record.body,
      status: "pending_review",
    });
  }

  return "inserted";
}

async function main() {
  console.log("Fetching outreach-ready dataset from Apify…");
  const records = await fetchOutreachDataset();
  console.log(`Fetched ${records.length} records`);

  let inserted = 0;
  let skipped = 0;
  let errors = 0;

  for (const record of records) {
    try {
      const result = await syncRecord(record);
      if (result === "inserted") inserted++;
      else skipped++;
    } catch (err) {
      console.error(`Failed to sync record ${record.signal_id}:`, err);
      errors++;
    }
  }

  console.log(`Sync complete: ${inserted} upserted, ${skipped} skipped (sent/archived), ${errors} errors`);
  await sql.end();
}

main().catch((err) => {
  console.error("Sync failed:", err);
  process.exit(1);
});
