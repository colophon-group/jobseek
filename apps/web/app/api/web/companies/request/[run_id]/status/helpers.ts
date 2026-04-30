/**
 * Helpers for the run-status proxy route. Lives in a sibling module so the
 * route's vitest spec can mock the DB lookup without faking the entire
 * Drizzle layer.
 *
 * @see colophon-group/jobseek#2810
 */
import "server-only";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { murmurAcceptLog, company } from "@/db/schema";

/**
 * Look up the catalog `company.slug` + `company_id` for a given Murmur run
 * id, if the webhook handler has already landed. Returns `null` when no
 * accept-log row exists yet.
 *
 * The accept-log row is the only piece of jobseek state that's
 * authoritatively keyed on `run_id`, so the proxy reads it directly
 * instead of trying to parse Murmur's `agent_actions`.
 */
export async function lookupAcceptedCompany(
  runId: string,
): Promise<{ slug: string | null; companyId: string | null } | null> {
  const rows = await db
    .select({
      companyId: murmurAcceptLog.companyId,
      slug: company.slug,
    })
    .from(murmurAcceptLog)
    .leftJoin(company, eq(company.id, murmurAcceptLog.companyId))
    .where(eq(murmurAcceptLog.runId, runId))
    .limit(1);

  if (rows.length === 0) return null;
  const row = rows[0];
  return {
    slug: row.slug ?? null,
    companyId: row.companyId ?? null,
  };
}
