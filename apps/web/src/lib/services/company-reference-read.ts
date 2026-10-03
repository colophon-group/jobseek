import "server-only";

import { inArray } from "drizzle-orm";
import { db } from "@/db";
import { companyReference } from "@/db/schema";
import { fetchCanonicalCompanyReferences } from "@/lib/services/company-references";
import { normalizeWatchlistUuid, WATCHLIST_COMPANY_MAX } from "@/lib/services/watchlist-input";

export type DisplayCompanyReference = {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
  unavailable?: boolean;
};

/** Read-only hydration: an unresolved selection keeps its identity and scope. */
export async function readCompanyReferences(ids: string[]): Promise<DisplayCompanyReference[]> {
  const normalized = ids.map(normalizeWatchlistUuid);
  if (normalized.some((id) => id === null)) throw new Error("invalid_company");
  const orderedIds = [...new Set(normalized as string[])];
  const byId = new Map<string, DisplayCompanyReference>();
  for (let offset = 0; offset < orderedIds.length; offset += WATCHLIST_COMPANY_MAX) {
    const batch = orderedIds.slice(offset, offset + WATCHLIST_COMPANY_MAX);
    const retained = await db.select({
      id: companyReference.id,
      name: companyReference.name,
      slug: companyReference.slug,
      icon: companyReference.icon,
    }).from(companyReference).where(inArray(companyReference.id, batch));
    retained.forEach((row) => byId.set(row.id, row));
    const missing = batch.filter((id) => !byId.has(id));
    if (missing.length === 0) continue;
    try {
      const canonical = await fetchCanonicalCompanyReferences(missing);
      canonical.forEach(({ id, name, slug, icon }) => byId.set(id, { id, name, slug, icon }));
    } catch {
      // The picker can outlive catalogue availability. Do not widen its scope
      // or materialize unverified references as a side effect of a page read.
    }
  }
  return orderedIds.map((id) => byId.get(id) ?? {
    id, name: "Company unavailable", slug: "", icon: null, unavailable: true,
  });
}
