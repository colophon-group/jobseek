import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { getTableConfig } from "drizzle-orm/pg-core";
import { describe, expect, it } from "vitest";

import { savedJob } from "@/db/schema";

import {
  isExactSavedJobTextCheck,
  SAVED_JOB_TEXT_CHECK_DEFINITION,
} from "../../../scripts/saved-job-contract-definition";

const migration = readFileSync(
  resolve(process.cwd(), "drizzle/0085_saved_job_snapshot_contract.sql"),
  "utf8",
);
const verifier = readFileSync(
  resolve(process.cwd(), "scripts/verify-saved-job-contract.ts"),
  "utf8",
);
const applicationTrackerMigration = readFileSync(
  resolve(process.cwd(), "drizzle/0068_application_tracker.sql"),
  "utf8",
);
const backupScript = readFileSync(
  resolve(process.cwd(), "../../scripts/jobseek-data-backup.py"),
  "utf8",
);

describe("0085 saved-job snapshot contract", () => {
  it("accepts only the exact 0084 prestate and locks source to child", () => {
    expect(migration).toContain("ledger_count <> 74");
    expect(migration).toContain("1785753600000");
    expect(migration).toContain(
      "e42314d98708bdced560abcc1d8f6c3abd6c58b7f467cebcf5e0cb1decc567dc",
    );

    const companyLock = migration.indexOf(
      "LOCK TABLE public.company IN SHARE MODE",
    );
    const postingLock = migration.indexOf(
      "LOCK TABLE public.job_posting IN SHARE MODE",
    );
    const savedJobLock = migration.indexOf(
      "LOCK TABLE public.saved_job IN SHARE ROW EXCLUSIVE MODE",
    );
    expect(companyLock).toBeGreaterThan(0);
    expect(postingLock).toBeGreaterThan(companyLock);
    expect(savedJobLock).toBeGreaterThan(postingLock);
  });

  it("catches up only missing required values without refreshing snapshots", () => {
    const update = migration.slice(
      migration.indexOf("UPDATE public.saved_job AS saved"),
      migration.indexOf("DO $contract$", migration.indexOf("UPDATE public.saved_job AS saved")),
    );

    for (const column of [
      "posting_title",
      "posting_source_url",
      "posting_first_seen_at",
      "posting_is_active",
      "company_id",
      "company_name",
      "company_slug",
    ]) {
      expect(update).toContain(`${column} = COALESCE`);
      expect(update).toContain(`saved.${column} IS NULL`);
    }
    for (const optional of [
      "posting_salary_min",
      "posting_salary_max",
      "posting_salary_currency",
      "posting_salary_period",
      "company_icon",
    ]) {
      expect(update).not.toContain(optional);
    }
  });

  it("makes exactly seven fields required and keeps optional snapshots nullable", () => {
    const config = getTableConfig(savedJob);
    const columns = new Map(
      config.columns.map((column) => [column.name, column.notNull]),
    );
    for (const required of [
      "posting_title",
      "posting_source_url",
      "posting_first_seen_at",
      "posting_is_active",
      "company_id",
      "company_name",
      "company_slug",
    ]) {
      expect(columns.get(required)).toBe(true);
    }
    for (const optional of [
      "posting_salary_min",
      "posting_salary_max",
      "posting_salary_currency",
      "posting_salary_period",
      "company_icon",
    ]) {
      expect(columns.get(optional)).toBe(false);
    }
    expect(
      config.checks.find(
        (constraint) => constraint.name === "saved_job_snapshot_text_nonblank_check",
      ),
    ).toBeDefined();
  });

  it("requires the full canonical nonblank CHECK without weakening", () => {
    const trueOrWeakening = `CHECK (true OR ${SAVED_JOB_TEXT_CHECK_DEFINITION.slice("CHECK ".length)})`;
    const partialWeakening =
      "CHECK (NULLIF(btrim(posting_title), ''::text) IS NOT NULL)";

    expect(isExactSavedJobTextCheck(SAVED_JOB_TEXT_CHECK_DEFINITION)).toBe(true);
    expect(isExactSavedJobTextCheck(trueOrWeakening)).toBe(false);
    expect(isExactSavedJobTextCheck(partialWeakening)).toBe(false);
    expect(migration).toContain(
      `$definition$${SAVED_JOB_TEXT_CHECK_DEFINITION}$definition$`,
    );
    expect(backupScript).toContain(
      "_quote_literal(WEB_POSTGRES_SAVED_JOB_TEXT_CHECK_DEFINITION)",
    );
    expect(verifier).toContain("isExactSavedJobTextCheck(checks[0].definition)");
  });

  it("keeps job_posting_id default-free as well as uuid NOT NULL", () => {
    const config = getTableConfig(savedJob);
    const jobPostingId = config.columns.find(
      (column) => column.name === "job_posting_id",
    );

    expect(jobPostingId?.notNull).toBe(true);
    expect(jobPostingId?.hasDefault).toBe(false);
    expect(migration).toContain("default_value.oid IS NULL");
    expect(verifier).toContain("jobPostingId.defaultValue === null");
    expect(backupScript).toContain("OR default_value.oid IS NOT NULL");
    expect(backupScript).not.toContain(
      "expected_column.column_name <> 'job_posting_id'",
    );
  });

  it("drops only compatibility objects and removes the posting FK last", () => {
    const temporaryCheck = migration.lastIndexOf(
      "DROP CONSTRAINT saved_job_required_snapshot_check",
    );
    const trigger = migration.lastIndexOf(
      "DROP TRIGGER saved_job_snapshot_from_mirror_before_insert",
    );
    const compatibilityFunction = migration.lastIndexOf(
      "DROP FUNCTION public.saved_job_snapshot_from_mirror()",
    );
    const postingForeignKey = migration.lastIndexOf(
      "DROP CONSTRAINT saved_job_job_posting_id_job_posting_id_fk",
    );

    expect(temporaryCheck).toBeGreaterThan(0);
    expect(trigger).toBeGreaterThan(temporaryCheck);
    expect(compatibilityFunction).toBeGreaterThan(trigger);
    expect(postingForeignKey).toBeGreaterThan(compatibilityFunction);
    expect(migration).toContain("saved_job_user_id_user_id_fk");
    expect(migration).toContain("idx_sj_user_posting");
    expect(migration).toContain(
      "application_interview_saved_job_id_fkey",
    );
  });

  it("preserves the cascading interview FK under its PostgreSQL-assigned name", () => {
    expect(applicationTrackerMigration).toContain(
      '"saved_job_id" uuid NOT NULL REFERENCES "saved_job"("id") ON DELETE CASCADE',
    );
    expect(applicationTrackerMigration).not.toContain(
      "application_interview_saved_job_id_saved_job_id_fk",
    );
    expect(migration).toContain("application_interview_saved_job_id_fkey");
    expect(migration).toContain("constraint_row.confdeltype = 'c'");
    expect(migration).toContain("constraint_row.confupdtype = 'a'");
  });

  it("leaves transaction ownership to Drizzle", () => {
    const executable = migration
      .split("\n")
      .filter((line) => !line.trimStart().startsWith("--"))
      .join("\n");
    expect(executable).not.toMatch(/\b(?:BEGIN|COMMIT|ROLLBACK)\s*;/i);
  });

  it("keeps mirror reads inside preflight verification", () => {
    const preflightBranch = verifier.indexOf('if (mode === "preflight")');
    const postflightBranch = verifier.indexOf("} else {", preflightBranch);

    expect(preflightBranch).toBeGreaterThan(0);
    expect(postflightBranch).toBeGreaterThan(preflightBranch);
    expect(verifier.slice(preflightBranch, postflightBranch)).toContain(
      "LEFT JOIN public.job_posting",
    );
    expect(verifier.slice(postflightBranch)).not.toContain("public.job_posting");
  });

  it("appends a monotonic journal entry at index 73", () => {
    const journal = JSON.parse(
      readFileSync(resolve(process.cwd(), "drizzle/meta/_journal.json"), "utf8"),
    ) as { entries: { idx: number; when: number; tag: string }[] };
    expect(journal.entries.at(-1)).toEqual({
      idx: 73,
      version: "7",
      when: 1_785_757_200_000,
      tag: "0085_saved_job_snapshot_contract",
      breakpoints: true,
    });
  });
});
