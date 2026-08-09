import { normalizePostingTitle } from "@/lib/posting-title";

export type SavedJobSnapshotRow = {
  id: string;
  postingId: string;
  postingTitle: string | null;
  postingSourceUrl: string | null;
  postingFirstSeenAt: Date | null;
  postingIsActive: boolean | null;
  postingSalaryMin?: number | null;
  postingSalaryMax?: number | null;
  postingSalaryCurrency?: string | null;
  postingSalaryPeriod?: string | null;
  companyId: string | null;
  companyName: string | null;
  companySlug: string | null;
  companyIcon: string | null;
};

function requiredText(
  savedJobId: string,
  field: string,
  value: string | null,
): string {
  if (value == null || value.trim() === "") {
    throw new Error(`Saved job ${savedJobId} has incomplete ${field}`);
  }
  return value;
}

/**
 * Decode the transitional nullable columns as the required snapshot contract.
 * 0084 backfills and checks these values; throwing here makes any later drift
 * observable instead of fabricating broken links, slugs, or timestamps.
 */
export function decodeSavedJobSnapshot(row: SavedJobSnapshotRow) {
  const title = normalizePostingTitle(
    requiredText(row.id, "posting_title", row.postingTitle),
  );
  if (!title) throw new Error(`Saved job ${row.id} has incomplete posting_title`);
  if (
    !(row.postingFirstSeenAt instanceof Date) ||
    Number.isNaN(row.postingFirstSeenAt.getTime())
  ) {
    throw new Error(`Saved job ${row.id} has incomplete posting_first_seen_at`);
  }
  if (typeof row.postingIsActive !== "boolean") {
    throw new Error(`Saved job ${row.id} has incomplete posting_is_active`);
  }

  return {
    posting: {
      id: requiredText(row.id, "job_posting_id", row.postingId),
      title,
      sourceUrl: requiredText(
        row.id,
        "posting_source_url",
        row.postingSourceUrl,
      ),
      firstSeenAt: row.postingFirstSeenAt,
      isActive: row.postingIsActive,
      salaryMin: row.postingSalaryMin ?? null,
      salaryMax: row.postingSalaryMax ?? null,
      salaryCurrency: row.postingSalaryCurrency ?? null,
      salaryPeriod: row.postingSalaryPeriod ?? null,
    },
    company: {
      id: requiredText(row.id, "company_id", row.companyId),
      name: requiredText(row.id, "company_name", row.companyName),
      slug: requiredText(row.id, "company_slug", row.companySlug),
      icon: row.companyIcon,
    },
  };
}
