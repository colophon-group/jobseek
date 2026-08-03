import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { getTableName } from "drizzle-orm";
import { getTableConfig } from "drizzle-orm/pg-core";
import { describe, expect, it } from "vitest";

import { savedJob } from "@/db/schema";

const migration = readFileSync(
  resolve(process.cwd(), "drizzle/0084_saved_job_snapshot_expand.sql"),
  "utf8",
);

describe("0084 saved-job snapshot expand", () => {
  it("protects old-app inserts before backfilling", () => {
    const triggerPosition = migration.indexOf(
      "CREATE TRIGGER saved_job_snapshot_from_mirror_before_insert",
    );
    const backfillPosition = migration.indexOf("UPDATE public.saved_job AS sj");

    expect(triggerPosition).toBeGreaterThan(0);
    expect(backfillPosition).toBeGreaterThan(triggerPosition);
    expect(migration).toContain("BEFORE INSERT ON public.saved_job");
    expect(migration).toContain("incomplete required snapshot");
  });

  it("treats salary and icon as optional but verifies required identity", () => {
    const verification = migration.slice(migration.lastIndexOf("DO $snapshot$"));

    expect(verification).toContain("posting_title");
    expect(verification).toContain("posting_source_url");
    expect(verification).toContain("posting_first_seen_at");
    expect(verification).toContain("posting_is_active");
    expect(verification).toContain("company_id");
    expect(verification).toContain("company_name");
    expect(verification).toContain("company_slug");
    expect(verification).not.toContain("posting_salary_min");
    expect(verification).not.toContain("company_icon");
  });

  it("retains a restrictive outbound posting FK throughout expand", () => {
    expect(migration).toContain(
      "DROP CONSTRAINT saved_job_job_posting_id_job_posting_id_fk",
    );
    expect(migration).toContain("ON DELETE RESTRICT");
    expect(migration).toContain(
      "restrictive job_posting FK must remain until contract",
    );

    const config = getTableConfig(savedJob);
    expect(config.foreignKeys).toHaveLength(2);
    const postingForeignKey = config.foreignKeys.find(
        (foreignKey) =>
          getTableName(foreignKey.reference().foreignTable) === "job_posting",
    );
    expect(postingForeignKey?.onDelete).toBe("restrict");
  });

  it("validates a temporary required-snapshot check", () => {
    expect(migration).toContain("saved_job_required_snapshot_check");
    expect(migration).toContain("required snapshot CHECK is not validated");
  });

  it("registers only the expand migration after the baseline repair", () => {
    const journal = JSON.parse(
      readFileSync(resolve(process.cwd(), "drizzle/meta/_journal.json"), "utf8"),
    ) as { entries: { tag: string }[] };
    const tags = journal.entries.map((entry) => entry.tag);

    expect(tags.at(-2)).toBe("0083_reconcile_supabase_baseline");
    expect(tags.at(-1)).toBe("0084_saved_job_snapshot_expand");
    expect(tags).not.toContain("0085_saved_job_snapshot_contract");
  });
});
