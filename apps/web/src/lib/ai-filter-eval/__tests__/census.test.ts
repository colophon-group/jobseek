import { describe, expect, it } from "vitest";

import {
  CENSUS_COUNT_COLUMNS,
  CENSUS_FILTER_CLASSIFICATION_RULES,
  CENSUS_STATEMENT_TIMEOUT_SQL,
  MINIMUM_RELEASED_CELL_COUNT,
  READ_ONLY_TRANSACTION_SQL,
  VERIFY_READ_ONLY_SQL,
  VERIFY_TABLE_PRIVILEGES_SQL,
  WATCHLIST_FILTER_CENSUS_SQL,
  collectWatchlistFilterCensus,
  serializeCensus,
  type CensusExecutor,
  type CensusQuery,
} from "../census";
import {
  CENSUS_APPROVAL_ID_ENV,
  CENSUS_APPROVED_DATABASE_ENV,
  CENSUS_APPROVED_HOST_ENV,
  CENSUS_APPROVED_ROLE_ENV,
  CENSUS_CONFIRMATION_FLAG,
  CENSUS_DATABASE_ENV,
  CENSUS_RUN_ID_ENV,
  CENSUS_TLS_MODE,
  requireCensusInvocation,
} from "../../../../script/collect-ai-filter-eval-census";

const EXPECTED_DATABASE = {
  database: "jobseek",
  role: "eval",
  approvalId: "github-issue-8325-approved",
  runId: "af2-census-2026-09-11-01",
} as const;
const VALID_INVOCATION_ENV = {
  [CENSUS_DATABASE_ENV]:
    "postgresql://eval:test@example.test:5432/jobseek?sslmode=verify-full",
  [CENSUS_APPROVAL_ID_ENV]: EXPECTED_DATABASE.approvalId,
  [CENSUS_RUN_ID_ENV]: EXPECTED_DATABASE.runId,
  [CENSUS_APPROVED_HOST_ENV]: "example.test:5432",
  [CENSUS_APPROVED_DATABASE_ENV]: EXPECTED_DATABASE.database,
  [CENSUS_APPROVED_ROLE_ENV]: EXPECTED_DATABASE.role,
} as const;

function aggregateRow(
  overrides: Record<string, string> = {},
): Record<string, unknown> {
  return {
    population_watchlists: "100",
    company_scope_any_company: "15",
    company_scope_explicit_zero: "15",
    company_scope_explicit_one: "15",
    company_scope_explicit_two_to_five: "15",
    company_scope_explicit_six_to_twenty: "20",
    company_scope_explicit_twenty_one_or_more: "20",
    filter_dimensions_zero: "20",
    filter_dimensions_one: "20",
    filter_dimensions_two: "20",
    filter_dimensions_three: "20",
    filter_dimensions_four_or_more: "20",
    keyword_count_zero: "25",
    keyword_count_one: "25",
    keyword_count_two_to_three: "25",
    keyword_count_four_or_more: "25",
    filter_presence_location: "40",
    filter_presence_occupation: "40",
    filter_presence_seniority: "40",
    filter_presence_technology: "40",
    filter_presence_work_mode: "40",
    filter_presence_employment_type: "40",
    filter_presence_salary: "40",
    filter_presence_experience: "40",
    ...overrides,
  };
}

function fakeExecutor(options?: {
  readOnly?: unknown;
  defaultReadOnly?: unknown;
  ssl?: unknown;
  currentUser?: unknown;
  currentDatabase?: unknown;
  hasWritePrivilege?: boolean;
  roleSuperuser?: boolean;
  aggregateRows?: readonly Record<string, unknown>[];
}): { executor: CensusExecutor; statements: string[] } {
  const statements: string[] = [];
  const query: CensusQuery = async (statement) => {
    statements.push(statement);
    if (statement === READ_ONLY_TRANSACTION_SQL) return [];
    if (statement === VERIFY_READ_ONLY_SQL) {
      return [
        {
          transaction_read_only: options?.readOnly ?? "on",
          default_transaction_read_only: options?.defaultReadOnly ?? "on",
          current_user: options?.currentUser ?? EXPECTED_DATABASE.role,
          current_database:
            options?.currentDatabase ?? EXPECTED_DATABASE.database,
          ssl: options?.ssl ?? true,
        },
      ];
    }
    if (statement === VERIFY_TABLE_PRIVILEGES_SQL) {
      return [
        {
          role_superuser: options?.roleSuperuser ?? false,
          role_createdb: false,
          role_createrole: false,
          role_replication: false,
          role_bypassrls: false,
          database_create: false,
          public_schema_create: false,
          watchlist_insert: options?.hasWritePrivilege ?? false,
          watchlist_update: false,
          watchlist_delete: false,
          watchlist_truncate: false,
          watchlist_trigger: false,
          watchlist_references: false,
          watchlist_company_insert: false,
          watchlist_company_update: false,
          watchlist_company_delete: false,
          watchlist_company_truncate: false,
          watchlist_company_trigger: false,
          watchlist_company_references: false,
        },
      ];
    }
    if (statement === CENSUS_STATEMENT_TIMEOUT_SQL) return [];
    if (statement === WATCHLIST_FILTER_CENSUS_SQL) {
      return options?.aggregateRows ?? [aggregateRow()];
    }
    throw new Error("Unexpected test query");
  };

  return {
    statements,
    executor: {
      transaction: (work) => work(query),
    },
  };
}

function cell(
  census: Awaited<ReturnType<typeof collectWatchlistFilterCensus>>,
  metricName: string,
  bucket: string,
) {
  return census.metrics
    .find((metric) => metric.name === metricName)
    ?.buckets.find((candidate) => candidate.bucket === bucket);
}

describe("AI filter evaluation census", () => {
  it("requires the dedicated database URL and explicit confirmation", () => {
    expect(
      requireCensusInvocation([CENSUS_CONFIRMATION_FLAG], VALID_INVOCATION_ENV),
    ).toEqual({
      databaseUrl: VALID_INVOCATION_ENV[CENSUS_DATABASE_ENV],
      approvalId: VALID_INVOCATION_ENV[CENSUS_APPROVAL_ID_ENV],
      runId: VALID_INVOCATION_ENV[CENSUS_RUN_ID_ENV],
      approvedHost: VALID_INVOCATION_ENV[CENSUS_APPROVED_HOST_ENV],
      database: EXPECTED_DATABASE.database,
      role: EXPECTED_DATABASE.role,
    });
    expect(() =>
      requireCensusInvocation([], VALID_INVOCATION_ENV),
    ).toThrow(/confirm-read-only-production-census/);
    expect(() =>
      requireCensusInvocation([CENSUS_CONFIRMATION_FLAG], {
        ...VALID_INVOCATION_ENV,
        [CENSUS_DATABASE_ENV]: undefined,
      }),
    ).toThrow(`${CENSUS_DATABASE_ENV} must be set`);
    expect(() =>
      requireCensusInvocation([CENSUS_CONFIRMATION_FLAG], {
        ...VALID_INVOCATION_ENV,
        [CENSUS_DATABASE_ENV]: "https://example.test/db",
      }),
    ).toThrow(/PostgreSQL protocol/);
    expect(() =>
      requireCensusInvocation([CENSUS_CONFIRMATION_FLAG], {
        ...VALID_INVOCATION_ENV,
        [CENSUS_APPROVAL_ID_ENV]: undefined,
      }),
    ).toThrow(`${CENSUS_APPROVAL_ID_ENV} must be set`);
    expect(() =>
      requireCensusInvocation([CENSUS_CONFIRMATION_FLAG], {
        ...VALID_INVOCATION_ENV,
        [CENSUS_RUN_ID_ENV]: "run id with spaces",
      }),
    ).toThrow(`${CENSUS_RUN_ID_ENV} is invalid`);
    expect(() =>
      requireCensusInvocation([CENSUS_CONFIRMATION_FLAG], {
        ...VALID_INVOCATION_ENV,
        [CENSUS_DATABASE_ENV]:
          "postgresql://eval:test@other.example.test:5432/jobseek",
      }),
    ).toThrow(/does not match the approved target/);
    expect(() =>
      requireCensusInvocation([CENSUS_CONFIRMATION_FLAG], {
        ...VALID_INVOCATION_ENV,
        [CENSUS_DATABASE_ENV]:
          "postgresql://eval:test@example.test:5432/jobseek?sslmode=disable",
      }),
    ).toThrow(/certificate-verified TLS/);
    expect(() =>
      requireCensusInvocation([CENSUS_CONFIRMATION_FLAG], {
        ...VALID_INVOCATION_ENV,
        [CENSUS_DATABASE_ENV]:
          "postgresql://eval:test@example.test:5432/jobseek?sslmode=require",
      }),
    ).toThrow(/certificate-verified TLS/);
    expect(() =>
      requireCensusInvocation([CENSUS_CONFIRMATION_FLAG], {
        ...VALID_INVOCATION_ENV,
        [CENSUS_DATABASE_ENV]:
          "postgresql://eval:test@example.test:5432/jobseek",
      }),
    ).toThrow(/certificate-verified TLS/);
    expect(CENSUS_TLS_MODE).toBe("verify-full");
  });

  it("sets and verifies read-only mode before executing the aggregate query", async () => {
    const { executor, statements } = fakeExecutor();

    const census = await collectWatchlistFilterCensus(
      executor,
      EXPECTED_DATABASE,
      new Date("2026-09-11T12:00:00.000Z"),
    );

    expect(statements).toEqual([
      READ_ONLY_TRANSACTION_SQL,
      VERIFY_READ_ONLY_SQL,
      VERIFY_TABLE_PRIVILEGES_SQL,
      CENSUS_STATEMENT_TIMEOUT_SQL,
      WATCHLIST_FILTER_CENSUS_SQL,
    ]);
    expect(census).toMatchObject({
      schemaVersion: 2,
      classificationSemantics: "aggregate_shape_approximation_v1",
      collectedAt: "2026-09-11T12:00:00.000Z",
      provenance: {
        approvalId: EXPECTED_DATABASE.approvalId,
        runId: EXPECTED_DATABASE.runId,
      },
      privacy: {
        aggregateOnly: true,
        minimumReleasedCellCount: MINIMUM_RELEASED_CELL_COUNT,
        readOnlyTransactionVerified: true,
        populationTotalsReleased: false,
        complementarySuppression: true,
        readOnlyCredentialVerified: true,
      },
    });
  });

  it("fails closed before census SQL if transaction_read_only is not on", async () => {
    const { executor, statements } = fakeExecutor({ readOnly: "off" });

    await expect(
      collectWatchlistFilterCensus(executor, EXPECTED_DATABASE),
    ).rejects.toThrow(
      "Database transaction is not read-only",
    );
    expect(statements).toEqual([
      READ_ONLY_TRANSACTION_SQL,
      VERIFY_READ_ONLY_SQL,
    ]);
  });

  it("rejects invalid provenance before opening a transaction", async () => {
    const { executor, statements } = fakeExecutor();
    await expect(
      collectWatchlistFilterCensus(executor, {
        ...EXPECTED_DATABASE,
        runId: "bad run id",
      }),
    ).rejects.toThrow("approval or run identifier is invalid");
    expect(statements).toEqual([]);
  });

  it("fails closed on TLS, identity, defaults, or write privileges", async () => {
    await expect(
      collectWatchlistFilterCensus(
        fakeExecutor({ ssl: false }).executor,
        EXPECTED_DATABASE,
      ),
    ).rejects.toThrow("not using TLS");
    await expect(
      collectWatchlistFilterCensus(
        fakeExecutor({ currentUser: "writer" }).executor,
        EXPECTED_DATABASE,
      ),
    ).rejects.toThrow("identity does not match approval");
    await expect(
      collectWatchlistFilterCensus(
        fakeExecutor({ defaultReadOnly: "off" }).executor,
        EXPECTED_DATABASE,
      ),
    ).rejects.toThrow("not read-only by default");

    const { executor, statements } = fakeExecutor({
      hasWritePrivilege: true,
    });
    await expect(
      collectWatchlistFilterCensus(executor, EXPECTED_DATABASE),
    ).rejects.toThrow("write-capable privileges");
    expect(statements).toEqual([
      READ_ONLY_TRANSACTION_SQL,
      VERIFY_READ_ONLY_SQL,
      VERIFY_TABLE_PRIVILEGES_SQL,
    ]);
    await expect(
      collectWatchlistFilterCensus(
        fakeExecutor({ roleSuperuser: true }).executor,
        EXPECTED_DATABASE,
      ),
    ).rejects.toThrow("write-capable privileges");
  });

  it("applies primary and complementary suppression for one small cell", async () => {
    const { executor } = fakeExecutor({
      aggregateRows: [
        aggregateRow({
          company_scope_any_company: "5",
          company_scope_explicit_zero: "25",
        }),
      ],
    });
    const census = await collectWatchlistFilterCensus(
      executor,
      EXPECTED_DATABASE,
    );

    expect(
      cell(census, "watchlists_by_company_scope", "any_company"),
    ).toEqual({
      bucket: "any_company",
      count: null,
      suppressed: true,
    });
    expect(
      cell(census, "watchlists_by_company_scope", "explicit_one"),
    ).toEqual({
      bucket: "explicit_one",
      count: null,
      suppressed: true,
    });
    expect(
      cell(census, "watchlists_by_filter_dimension_count", "zero"),
    ).toMatchObject({ count: null, suppressed: true });
    expect(
      cell(census, "watchlists_by_filter_dimension_count", "one"),
    ).toMatchObject({ count: null, suppressed: true });
    expect(census.metrics.map((metric) => metric.name)).not.toContain(
      "watchlist_population",
    );
  });

  it("keeps multiple small cells hidden without exposing a recoverable total", async () => {
    const { executor } = fakeExecutor({
      aggregateRows: [
        aggregateRow({
          company_scope_any_company: "4",
          company_scope_explicit_zero: "6",
          company_scope_explicit_one: "35",
        }),
      ],
    });
    const census = await collectWatchlistFilterCensus(
      executor,
      EXPECTED_DATABASE,
    );

    expect(
      cell(census, "watchlists_by_company_scope", "any_company"),
    ).toMatchObject({ count: null, suppressed: true });
    expect(
      cell(census, "watchlists_by_company_scope", "explicit_zero"),
    ).toMatchObject({ count: null, suppressed: true });
    expect(
      cell(census, "watchlists_by_company_scope", "explicit_one"),
    ).toMatchObject({ count: 35, suppressed: false });
    expect(census.metrics.flatMap((metric) => metric.buckets)).not.toContainEqual(
      expect.objectContaining({ bucket: "all_persisted_watchlists" }),
    );
  });

  it("protects a small implied complement in a presence marginal", async () => {
    const { executor } = fakeExecutor({
      aggregateRows: [
        aggregateRow({
          filter_presence_location: "95",
        }),
      ],
    });
    const census = await collectWatchlistFilterCensus(
      executor,
      EXPECTED_DATABASE,
    );

    expect(
      cell(census, "watchlists_by_filter_presence", "location"),
    ).toMatchObject({ count: null, suppressed: true });
    expect(
      cell(census, "watchlists_by_filter_presence", "occupation"),
    ).toMatchObject({ count: null, suppressed: true });
  });

  it("emits only fixed ordered metric and bucket labels", async () => {
    const { executor } = fakeExecutor();
    const census = await collectWatchlistFilterCensus(
      executor,
      EXPECTED_DATABASE,
    );

    expect(census.metrics.map((metric) => metric.name)).toEqual([
      "watchlists_by_company_scope",
      "watchlists_by_filter_dimension_count",
      "watchlists_by_keyword_count",
      "watchlists_by_filter_presence",
    ]);
    expect(
      census.metrics.find(
        (metric) => metric.name === "watchlists_by_company_scope",
      )?.buckets,
    ).toEqual([
      { bucket: "any_company", count: 15, suppressed: false },
      { bucket: "explicit_zero", count: 15, suppressed: false },
      { bucket: "explicit_one", count: 15, suppressed: false },
      { bucket: "explicit_two_to_five", count: 15, suppressed: false },
      { bucket: "explicit_six_to_twenty", count: 20, suppressed: false },
      {
        bucket: "explicit_twenty_one_or_more",
        count: 20,
        suppressed: false,
      },
    ]);
  });

  it("rejects aggregate rows with missing, extra, or invalid cells", async () => {
    const privateKeyword = "staff platform engineer near secret-project";
    const extra = aggregateRow({ raw_keyword_text: privateKeyword });
    const missing = aggregateRow();
    delete missing.population_watchlists;

    await expect(
      collectWatchlistFilterCensus(
        fakeExecutor({ aggregateRows: [extra] }).executor,
        EXPECTED_DATABASE,
      ),
    ).rejects.toThrow("unexpected aggregate shape");
    expect(CENSUS_COUNT_COLUMNS).not.toContain("raw_keyword_text");
    await expect(
      collectWatchlistFilterCensus(
        fakeExecutor({ aggregateRows: [missing] }).executor,
        EXPECTED_DATABASE,
      ),
    ).rejects.toThrow("unexpected aggregate shape");
    await expect(
      collectWatchlistFilterCensus(
        fakeExecutor({
          aggregateRows: [aggregateRow({ population_watchlists: "9.5" })],
        }).executor,
        EXPECTED_DATABASE,
      ),
    ).rejects.toThrow("invalid aggregate count");
    await expect(
      collectWatchlistFilterCensus(
        fakeExecutor({
          aggregateRows: [
            aggregateRow({ company_scope_any_company: "16" }),
          ],
        }).executor,
        EXPECTED_DATABASE,
      ),
    ).rejects.toThrow("inconsistent aggregate counts");
  });

  it("pins the effective-read-derived classifier without claiming parity", () => {
    const normalization = CENSUS_FILTER_CLASSIFICATION_RULES;

    expect(normalization).toEqual({
      keyword: { maxItems: 20, maxLength: 120 },
      taxonomy: { maxItems: 20, maxLength: 100 },
      workMode: { maxItems: 3, maxLength: 8 },
      employmentType: { maxItems: 6, maxLength: 16 },
      salaryMaximum: 1_000_000_000,
      experienceMaximum: 15,
    });
    expect(WATCHLIST_FILTER_CENSUS_SQL).toContain(
      "array_item.item_index <= list_rules.max_items",
    );
    expect(WATCHLIST_FILTER_CENSUS_SQL).toContain(
      "jsonb_typeof(item.value) = 'string'",
    );
    expect(WATCHLIST_FILTER_CENSUS_SQL).toContain("count(DISTINCT CASE");
    expect(WATCHLIST_FILTER_CENSUS_SQL).toContain(
      "ARRAY['onsite', 'hybrid', 'remote']::text[]",
    );
    expect(WATCHLIST_FILTER_CENSUS_SQL).toContain(
      "ARRAY['full_time', 'part_time', 'contract', 'internship', 'temporary', 'volunteer']::text[]",
    );
    expect(WATCHLIST_FILTER_CENSUS_SQL).toContain(
      "salary_min > salary_max THEN 0",
    );
    expect(WATCHLIST_FILTER_CENSUS_SQL).toContain(
      "experience_min > experience_max THEN 0",
    );
    expect(WATCHLIST_FILTER_CENSUS_SQL).not.toContain(
      "normalizeWatchlistFiltersForRead",
    );
  });

  it("serializes deterministically for a fixed collection timestamp", async () => {
    const collectedAt = new Date("2026-09-11T12:00:00.000Z");
    const first = await collectWatchlistFilterCensus(
      fakeExecutor().executor,
      EXPECTED_DATABASE,
      collectedAt,
    );
    const second = await collectWatchlistFilterCensus(
      fakeExecutor().executor,
      EXPECTED_DATABASE,
      collectedAt,
    );

    expect(serializeCensus(first)).toBe(serializeCensus(second));
    expect(serializeCensus(first)).toBe(
      `${JSON.stringify(first, null, 2)}\n`,
    );
  });

  it("contains only transaction controls and aggregate read SQL", () => {
    const sql = [
      READ_ONLY_TRANSACTION_SQL,
      VERIFY_READ_ONLY_SQL,
      VERIFY_TABLE_PRIVILEGES_SQL,
      CENSUS_STATEMENT_TIMEOUT_SQL,
      WATCHLIST_FILTER_CENSUS_SQL,
    ].join("\n");

    expect(WATCHLIST_FILTER_CENSUS_SQL).toContain("FROM public.watchlist");
    expect(WATCHLIST_FILTER_CENSUS_SQL).toContain("public.watchlist_company");
    expect(WATCHLIST_FILTER_CENSUS_SQL).not.toMatch(
      /\b(?:insert|update|delete|merge|copy|create|alter|drop|truncate|call|do)\b/i,
    );
    expect(sql).not.toMatch(/\b(?:title|description|created_at|updated_at)\b/i);
    expect(
      CENSUS_COUNT_COLUMNS.every(
        (column) =>
          !/(?:^|_)(?:user_id|watchlist_id|company_id|name|title|description|slug|raw_keyword_text|timestamp)(?:_|$)/.test(
            column,
          ),
      ),
    ).toBe(true);
  });
});
