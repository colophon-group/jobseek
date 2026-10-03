import "server-only";

import { eq, inArray, sql } from "drizzle-orm";
import { db } from "@/db";
import { company, companyReference } from "@/db/schema";
import { isUniqueViolation } from "@/lib/db-conflict";
import { logExternalError } from "@/lib/safe-external-error";
import { getSearchClient } from "@/lib/search/typesense-client";
import { assertTypesenseSearchResult, withTypesenseRetry } from "@/lib/search/typesense-retry";
import { isSafeCompanySlug } from "@/lib/services/company-detail-lookup";
import { normalizeWatchlistUuid, WATCHLIST_COMPANY_MAX } from "@/lib/services/watchlist-input";

export type CompanyReferenceErrorCode =
  | "invalid_company"
  | "unknown_company"
  | "company_lookup_unavailable"
  | "company_identity_conflict";

/** Content-free errors can safely cross Server Action and logging boundaries. */
export class CompanyReferenceError extends Error {
  constructor(public readonly code: CompanyReferenceErrorCode) {
    super(code);
    this.name = "CompanyReferenceError";
  }
}

export function companyReferenceErrorResult(error: unknown): { error: CompanyReferenceErrorCode } {
  if (!(error instanceof CompanyReferenceError)) throw error;
  logExternalError("warn", { service: "database", operation: `company_reference_${error.code}` }, error);
  return { error: error.code };
}

export type PreparedCompanyReference = {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
  source: "legacy_seed" | "typesense";
  verifiedAt: Date | null;
};

type CompanyTransaction = Parameters<Parameters<typeof db.transaction>[0]>[0];
type ReferenceReader = Pick<typeof db, "select">;
// Same display-text control policy as the watchlist read normalizer. NUL in
// particular is rejected before it can reach PostgreSQL's text parameters.
const TEXT_CONTROL_CHARACTERS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

function normalizeIds(ids: readonly string[]): string[] {
  if (ids.length > WATCHLIST_COMPANY_MAX) throw new CompanyReferenceError("invalid_company");
  const normalized = ids.map((id) => normalizeWatchlistUuid(id));
  if (normalized.some((id) => id === null)) throw new CompanyReferenceError("invalid_company");
  return [...new Set(normalized as string[])];
}

/** Validate only the server-owned display snapshot, never browser metadata. */
function validateReference(document: unknown, expectedId: string): Omit<PreparedCompanyReference, "source" | "verifiedAt"> {
  if (!document || typeof document !== "object") {
    throw new CompanyReferenceError("company_lookup_unavailable");
  }
  const value = document as Record<string, unknown>;
  if (value.id !== expectedId) throw new CompanyReferenceError("company_identity_conflict");
  if (
    typeof value.name !== "string" || !value.name.trim() || value.name.length > 300 || TEXT_CONTROL_CHARACTERS.test(value.name) ||
    typeof value.slug !== "string" || value.slug.length > 100 || !isSafeCompanySlug(value.slug) ||
    (value.icon != null && (typeof value.icon !== "string" || value.icon.length > 2048 || TEXT_CONTROL_CHARACTERS.test(value.icon)))
  ) {
    throw new CompanyReferenceError("company_lookup_unavailable");
  }
  return { id: expectedId, name: value.name.trim(), slug: value.slug, icon: value.icon == null ? null : (value.icon as string).trim() || null };
}

async function readExistingReferences(reader: ReferenceReader, ids: readonly string[]): Promise<PreparedCompanyReference[]> {
  if (ids.length === 0) return [];
  return reader.select({
    id: companyReference.id,
    name: companyReference.name,
    slug: companyReference.slug,
    icon: companyReference.icon,
    source: companyReference.source,
    verifiedAt: companyReference.verifiedAt,
  }).from(companyReference).where(inArray(companyReference.id, [...ids]));
}

/**
 * Resolve new identities before beginning a mutation transaction. Existing
 * durable references remain usable after catalogue retirement or search outage.
 * This function never writes, including when a later member of a batch fails.
 */
export async function prepareCompanyReferences(ids: readonly string[]): Promise<PreparedCompanyReference[]> {
  const normalized = normalizeIds(ids);
  const existing = await readExistingReferences(db, normalized);
  const byId = new Map(existing.map((row) => [row.id, row]));
  const missing = normalized.filter((id) => !byId.has(id));
  const canonical = await fetchCanonicalCompanyReferences(missing);
  if (canonical.length !== missing.length) throw new CompanyReferenceError("unknown_company");
  canonical.forEach((reference) => byId.set(reference.id, reference));
  return normalized.map((id) => byId.get(id)!);
}

/** Canonical read-only snapshots. Absence is allowed; malformed identity is not. */
export async function fetchCanonicalCompanyReferences(ids: readonly string[]): Promise<PreparedCompanyReference[]> {
  const missing = normalizeIds(ids);
  const byId = new Map<string, PreparedCompanyReference>();
  const verifiedAt = new Date();

  // UUIDs have a fixed safe filter alphabet. Bound request size and response
  // size; partial, duplicated, or foreign documents fail the entire mutation.
  for (let offset = 0; offset < missing.length; offset += 50) {
    const batch = missing.slice(offset, offset + 50);
    let result: unknown;
    try {
      result = await withTypesenseRetry(() => getSearchClient()
        .collections("company").documents().search({
          q: "*",
          filter_by: `id:=[${batch.join(",")}]`,
          per_page: batch.length,
          include_fields: "id,name,slug,icon",
        }), { label: "prepare_company_references" });
      assertTypesenseSearchResult(result, { expectHits: true });
    } catch {
      throw new CompanyReferenceError("company_lookup_unavailable");
    }
    const response = result as { found: number; hits?: Array<{ document: Record<string, unknown> }> };
    const seen = new Set<string>();
    for (const hit of response.hits ?? []) {
      const id = hit.document.id;
      if (typeof id !== "string" || !batch.includes(id) || seen.has(id)) {
        throw new CompanyReferenceError("company_identity_conflict");
      }
      seen.add(id);
      byId.set(id, { ...validateReference(hit.document, id), source: "typesense", verifiedAt });
    }
    // A valid short response means the requested company no longer exists.
    // An incomplete/paginated response is a provider failure, never a removal.
    if (response.found !== seen.size) throw new CompanyReferenceError("company_lookup_unavailable");
  }
  return missing.flatMap((id) => byId.has(id) ? [byId.get(id)!] : []);
}

function writesLegacyBridge(): boolean {
  const mode = process.env.COMPANY_REFERENCE_WRITE_MODE ?? "bridge";
  if (mode !== "bridge" && mode !== "reference") {
    throw new Error("Invalid company reference write mode");
  }
  return mode === "bridge";
}

/**
 * Materialize both representations in the SAME transaction as the selection.
 * ID conflicts are idempotent; a legacy slug assigned to a different canonical
 * UUID is a hard conflict, never an opportunity to remap someone's selection.
 */
export async function persistCompanyReferences(tx: CompanyTransaction, references: readonly PreparedCompanyReference[]): Promise<void> {
  const bridge = writesLegacyBridge();
  // Sorted insertion avoids reversed lock order between overlapping batches.
  const sorted = [...references].sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
  for (const reference of sorted) {
    // Legacy INSERT/UPDATE holds a company row before its AFTER trigger takes
    // the reference row. Use the same lock order during coexistence; taking
    // reference first would deadlock against the old writer for the same UUID.
    if (bridge) {
      try {
        await tx.insert(company).values({
          id: reference.id, name: reference.name, slug: reference.slug, icon: reference.icon,
        }).onConflictDoNothing({ target: company.id });
      } catch (error) {
        if (isUniqueViolation(error, "company_slug_unique") || isUniqueViolation(error, "company_slug_key")) {
          throw new CompanyReferenceError("company_identity_conflict");
        }
        throw error;
      }
    }
    if (reference.source === "typesense") {
      // The compatibility trigger may seed a legacy row during preparation
      // or immediately above. Promote that snapshot in the same transaction;
      // never overwrite a canonical snapshot another request already persisted.
      await tx.insert(companyReference).values(reference).onConflictDoUpdate({
        target: companyReference.id,
        set: {
          name: reference.name, slug: reference.slug, icon: reference.icon,
          source: "typesense", verifiedAt: reference.verifiedAt, updatedAt: new Date(),
        },
        setWhere: eq(companyReference.source, "legacy_seed"),
      });
    } else {
      await tx.insert(companyReference).values(reference).onConflictDoNothing({ target: companyReference.id });
    }
  }
}

/** Copy uses already-persisted references under its source lock; no network. */
export async function persistExistingCompanyReferences(tx: CompanyTransaction, ids: readonly string[]): Promise<void> {
  const normalized = normalizeIds(ids);
  const references = await readExistingReferences(tx, normalized);
  if (references.length !== normalized.length) throw new CompanyReferenceError("unknown_company");
  await persistCompanyReferences(tx, references);
}

/** Serialize a user's star toggle without retaining a lock across search I/O. */
export async function lockCompanyStar(tx: CompanyTransaction, userId: string, companyId: string): Promise<void> {
  await tx.execute(sql`SELECT pg_advisory_xact_lock(hashtextextended(${`${userId}:${companyId}`}, 10225::bigint))`);
}
