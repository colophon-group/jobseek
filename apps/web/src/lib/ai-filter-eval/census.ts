import {
  EMPLOYMENT_TYPE_VALUES,
  WORK_MODE_VALUES,
} from "@/lib/search/types";

export const CENSUS_SCHEMA_VERSION = 2 as const;
export const MINIMUM_RELEASED_CELL_COUNT = 10;

export const READ_ONLY_TRANSACTION_SQL =
  "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY";
export const VERIFY_READ_ONLY_SQL = String.raw`
SELECT
  current_setting('transaction_read_only') AS transaction_read_only,
  current_setting('default_transaction_read_only') AS default_transaction_read_only,
  current_user AS current_user,
  current_database() AS current_database,
  coalesce(
    (SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid = pg_backend_pid()),
    false
  ) AS ssl
`;
export const VERIFY_TABLE_PRIVILEGES_SQL = String.raw`
SELECT
  roles.rolsuper AS role_superuser,
  roles.rolcreatedb AS role_createdb,
  roles.rolcreaterole AS role_createrole,
  roles.rolreplication AS role_replication,
  roles.rolbypassrls AS role_bypassrls,
  has_database_privilege(current_user, current_database(), 'CREATE') AS database_create,
  has_schema_privilege(current_user, 'public', 'CREATE') AS public_schema_create,
  has_table_privilege(current_user, 'public.watchlist', 'INSERT') AS watchlist_insert,
  has_table_privilege(current_user, 'public.watchlist', 'UPDATE') AS watchlist_update,
  has_table_privilege(current_user, 'public.watchlist', 'DELETE') AS watchlist_delete,
  has_table_privilege(current_user, 'public.watchlist', 'TRUNCATE') AS watchlist_truncate,
  has_table_privilege(current_user, 'public.watchlist', 'TRIGGER') AS watchlist_trigger,
  has_table_privilege(current_user, 'public.watchlist', 'REFERENCES') AS watchlist_references,
  has_table_privilege(current_user, 'public.watchlist_company', 'INSERT') AS watchlist_company_insert,
  has_table_privilege(current_user, 'public.watchlist_company', 'UPDATE') AS watchlist_company_update,
  has_table_privilege(current_user, 'public.watchlist_company', 'DELETE') AS watchlist_company_delete,
  has_table_privilege(current_user, 'public.watchlist_company', 'TRUNCATE') AS watchlist_company_truncate,
  has_table_privilege(current_user, 'public.watchlist_company', 'TRIGGER') AS watchlist_company_trigger,
  has_table_privilege(current_user, 'public.watchlist_company', 'REFERENCES') AS watchlist_company_references
FROM pg_catalog.pg_roles AS roles
WHERE roles.rolname = current_user
`;
export const CENSUS_STATEMENT_TIMEOUT_SQL =
  "SET LOCAL statement_timeout = '30s'";

/**
 * Fixed aggregate-classification rules derived from the effective read path.
 * Keeping them exported makes the SQL contract easy to inspect and pin.
 */
export const CENSUS_FILTER_CLASSIFICATION_RULES = {
  keyword: { maxItems: 20, maxLength: 120 },
  taxonomy: { maxItems: 20, maxLength: 100 },
  workMode: { maxItems: WORK_MODE_VALUES.length, maxLength: 8 },
  employmentType: {
    maxItems: EMPLOYMENT_TYPE_VALUES.length,
    maxLength: 16,
  },
  salaryMaximum: 1_000_000_000,
  experienceMaximum: 15,
} as const;

function sqlTextArray(values: readonly string[]): string {
  return `ARRAY[${values.map((value) => `'${value.replaceAll("'", "''")}'`).join(", ")}]::text[]`;
}

const rules = CENSUS_FILTER_CLASSIFICATION_RULES;
const workModesSql = sqlTextArray(WORK_MODE_VALUES);
const employmentTypesSql = sqlTextArray(EMPLOYMENT_TYPE_VALUES);

/**
 * One aggregate row crosses the database boundary. Per-watchlist identifiers
 * and filter values exist only in PostgreSQL CTEs and are never returned.
 *
 * This is an aggregate shape classifier, not a compatibility implementation
 * of normalizeWatchlistFiltersForRead. It follows the material effective-read
 * rules (bounded leading items, validation, deduplication, public enums,
 * bounded numeric ranges, and reversed-range removal). PostgreSQL trim,
 * character length, case-folding, and POSIX Unicode classes can differ from
 * JavaScript on unusual Unicode input. Candidate extraction must therefore
 * use the application normalizer; this census is only for stratification.
 */
export const WATCHLIST_FILTER_CENSUS_SQL = String.raw`
WITH list_rules(
  field_name,
  max_items,
  max_length,
  case_insensitive,
  slug_only,
  allowed_values
) AS (
  VALUES
    ('keywords', ${rules.keyword.maxItems}, ${rules.keyword.maxLength}, true, false, NULL::text[]),
    ('locationSlugs', ${rules.taxonomy.maxItems}, ${rules.taxonomy.maxLength}, false, true, NULL::text[]),
    ('occupationSlugs', ${rules.taxonomy.maxItems}, ${rules.taxonomy.maxLength}, false, true, NULL::text[]),
    ('senioritySlugs', ${rules.taxonomy.maxItems}, ${rules.taxonomy.maxLength}, false, true, NULL::text[]),
    ('technologySlugs', ${rules.taxonomy.maxItems}, ${rules.taxonomy.maxLength}, false, true, NULL::text[]),
    ('workMode', ${rules.workMode.maxItems}, ${rules.workMode.maxLength}, false, false, ${workModesSql}),
    ('employmentType', ${rules.employmentType.maxItems}, ${rules.employmentType.maxLength}, false, false, ${employmentTypesSql})
),
company_counts AS (
  SELECT
    watchlist_id,
    count(*)::integer AS company_count
  FROM public.watchlist_company
  GROUP BY watchlist_id
),
watchlist_shapes AS (
  SELECT
    w.id AS watchlist_id,
    CASE
      WHEN jsonb_typeof(w.filters) = 'object' THEN w.filters
      ELSE '{}'::jsonb
    END AS filters,
    coalesce(company_counts.company_count, 0) AS company_count
  FROM public.watchlist AS w
  LEFT JOIN company_counts ON company_counts.watchlist_id = w.id
),
list_counts_long AS (
  SELECT
    watchlist_shapes.watchlist_id,
    list_rules.field_name,
    count(DISTINCT CASE
      WHEN jsonb_typeof(item.value) = 'string'
        AND char_length(item.normalized) BETWEEN 1 AND list_rules.max_length
        AND regexp_replace(item.normalized, E'[\\t\\n\\r]', '', 'g') !~ '[[:cntrl:]]'
        AND (
          NOT list_rules.slug_only
          OR item.normalized ~ '^[[:alnum:]][[:alnum:]._-]*$'
        )
        AND (
          list_rules.allowed_values IS NULL
          OR item.normalized = ANY(list_rules.allowed_values)
        )
      THEN CASE
        WHEN list_rules.case_insensitive THEN lower(item.normalized)
        ELSE item.normalized
      END
    END)::integer AS item_count
  FROM watchlist_shapes
  CROSS JOIN list_rules
  LEFT JOIN LATERAL (
    SELECT
      array_item.value,
      btrim(array_item.value #>> '{}', E' \\t\\n\\r\\f\\v') AS normalized
    FROM jsonb_array_elements(
      CASE
        WHEN jsonb_typeof(watchlist_shapes.filters -> list_rules.field_name) = 'array'
          THEN watchlist_shapes.filters -> list_rules.field_name
        ELSE '[]'::jsonb
      END
    ) WITH ORDINALITY AS array_item(value, item_index)
    WHERE array_item.item_index <= list_rules.max_items
  ) AS item ON true
  GROUP BY watchlist_shapes.watchlist_id, list_rules.field_name
),
list_counts AS (
  SELECT
    watchlist_id,
    max(item_count) FILTER (WHERE field_name = 'keywords') AS keyword_count,
    max(item_count) FILTER (WHERE field_name = 'locationSlugs') AS location_count,
    max(item_count) FILTER (WHERE field_name = 'occupationSlugs') AS occupation_count,
    max(item_count) FILTER (WHERE field_name = 'senioritySlugs') AS seniority_count,
    max(item_count) FILTER (WHERE field_name = 'technologySlugs') AS technology_count,
    max(item_count) FILTER (WHERE field_name = 'workMode') AS work_mode_count,
    max(item_count) FILTER (WHERE field_name = 'employmentType') AS employment_type_count
  FROM list_counts_long
  GROUP BY watchlist_id
),
normalized_ranges AS (
  SELECT
    watchlist_shapes.*,
    CASE
      WHEN jsonb_typeof(filters -> 'salaryMin') = 'number'
        AND (filters ->> 'salaryMin')::numeric BETWEEN 0 AND ${rules.salaryMaximum}
        THEN (filters ->> 'salaryMin')::numeric
    END AS salary_min,
    CASE
      WHEN jsonb_typeof(filters -> 'salaryMax') = 'number'
        AND (filters ->> 'salaryMax')::numeric BETWEEN 0 AND ${rules.salaryMaximum}
        THEN (filters ->> 'salaryMax')::numeric
    END AS salary_max,
    CASE
      WHEN jsonb_typeof(filters -> 'experienceMin') = 'number'
        AND (filters ->> 'experienceMin')::numeric BETWEEN 0 AND ${rules.experienceMaximum}
        AND (filters ->> 'experienceMin')::numeric = trunc((filters ->> 'experienceMin')::numeric)
        THEN (filters ->> 'experienceMin')::numeric
    END AS experience_min,
    CASE
      WHEN jsonb_typeof(filters -> 'experienceMax') = 'number'
        AND (filters ->> 'experienceMax')::numeric BETWEEN 0 AND ${rules.experienceMaximum}
        AND (filters ->> 'experienceMax')::numeric = trunc((filters ->> 'experienceMax')::numeric)
        THEN (filters ->> 'experienceMax')::numeric
    END AS experience_max
  FROM watchlist_shapes
),
classified AS (
  SELECT
    normalized_ranges.company_count,
    normalized_ranges.filters @> '{"anyCompany": true}'::jsonb AS any_company,
    coalesce(list_counts.keyword_count, 0) AS keyword_count,
    CASE WHEN coalesce(list_counts.keyword_count, 0) > 0 THEN 1 ELSE 0 END AS has_keywords,
    CASE WHEN coalesce(list_counts.location_count, 0) > 0 THEN 1 ELSE 0 END AS has_location,
    CASE WHEN coalesce(list_counts.occupation_count, 0) > 0 THEN 1 ELSE 0 END AS has_occupation,
    CASE WHEN coalesce(list_counts.seniority_count, 0) > 0 THEN 1 ELSE 0 END AS has_seniority,
    CASE WHEN coalesce(list_counts.technology_count, 0) > 0 THEN 1 ELSE 0 END AS has_technology,
    CASE WHEN coalesce(list_counts.work_mode_count, 0) > 0 THEN 1 ELSE 0 END AS has_work_mode,
    CASE WHEN coalesce(list_counts.employment_type_count, 0) > 0 THEN 1 ELSE 0 END AS has_employment_type,
    CASE
      WHEN salary_min IS NOT NULL AND salary_max IS NOT NULL AND salary_min > salary_max THEN 0
      WHEN salary_min IS NOT NULL OR salary_max IS NOT NULL THEN 1
      ELSE 0
    END AS has_salary,
    CASE
      WHEN experience_min IS NOT NULL AND experience_max IS NOT NULL
        AND experience_min > experience_max THEN 0
      WHEN experience_min IS NOT NULL OR experience_max IS NOT NULL THEN 1
      ELSE 0
    END AS has_experience
  FROM normalized_ranges
  LEFT JOIN list_counts ON list_counts.watchlist_id = normalized_ranges.watchlist_id
),
scored AS (
  SELECT
    *,
    has_keywords + has_location + has_occupation + has_seniority
      + has_technology + has_work_mode + has_employment_type
      + has_salary + has_experience AS filter_dimension_count
  FROM classified
)
SELECT
  (SELECT count(*)::text FROM scored) AS population_watchlists,

  count(*) FILTER (WHERE any_company)::text AS company_scope_any_company,
  count(*) FILTER (WHERE NOT any_company AND company_count = 0)::text
    AS company_scope_explicit_zero,
  count(*) FILTER (WHERE NOT any_company AND company_count = 1)::text
    AS company_scope_explicit_one,
  count(*) FILTER (WHERE NOT any_company AND company_count BETWEEN 2 AND 5)::text
    AS company_scope_explicit_two_to_five,
  count(*) FILTER (WHERE NOT any_company AND company_count BETWEEN 6 AND 20)::text
    AS company_scope_explicit_six_to_twenty,
  count(*) FILTER (WHERE NOT any_company AND company_count >= 21)::text
    AS company_scope_explicit_twenty_one_or_more,

  count(*) FILTER (WHERE filter_dimension_count = 0)::text AS filter_dimensions_zero,
  count(*) FILTER (WHERE filter_dimension_count = 1)::text AS filter_dimensions_one,
  count(*) FILTER (WHERE filter_dimension_count = 2)::text AS filter_dimensions_two,
  count(*) FILTER (WHERE filter_dimension_count = 3)::text AS filter_dimensions_three,
  count(*) FILTER (WHERE filter_dimension_count >= 4)::text AS filter_dimensions_four_or_more,

  count(*) FILTER (WHERE keyword_count = 0)::text AS keyword_count_zero,
  count(*) FILTER (WHERE keyword_count = 1)::text AS keyword_count_one,
  count(*) FILTER (WHERE keyword_count BETWEEN 2 AND 3)::text AS keyword_count_two_to_three,
  count(*) FILTER (WHERE keyword_count >= 4)::text AS keyword_count_four_or_more,

  count(*) FILTER (WHERE has_location = 1)::text AS filter_presence_location,
  count(*) FILTER (WHERE has_occupation = 1)::text AS filter_presence_occupation,
  count(*) FILTER (WHERE has_seniority = 1)::text AS filter_presence_seniority,
  count(*) FILTER (WHERE has_technology = 1)::text AS filter_presence_technology,
  count(*) FILTER (WHERE has_work_mode = 1)::text AS filter_presence_work_mode,
  count(*) FILTER (WHERE has_employment_type = 1)::text AS filter_presence_employment_type,
  count(*) FILTER (WHERE has_salary = 1)::text AS filter_presence_salary,
  count(*) FILTER (WHERE has_experience = 1)::text AS filter_presence_experience
FROM scored
`;

type CensusMetricDefinition = {
  name: string;
  exhaustive: boolean;
  buckets: readonly (readonly [bucket: string, column: string])[];
};

const CENSUS_METRICS: readonly CensusMetricDefinition[] = [
  {
    name: "watchlists_by_company_scope",
    exhaustive: true,
    buckets: [
      ["any_company", "company_scope_any_company"],
      ["explicit_zero", "company_scope_explicit_zero"],
      ["explicit_one", "company_scope_explicit_one"],
      ["explicit_two_to_five", "company_scope_explicit_two_to_five"],
      ["explicit_six_to_twenty", "company_scope_explicit_six_to_twenty"],
      [
        "explicit_twenty_one_or_more",
        "company_scope_explicit_twenty_one_or_more",
      ],
    ],
  },
  {
    name: "watchlists_by_filter_dimension_count",
    exhaustive: true,
    buckets: [
      ["zero", "filter_dimensions_zero"],
      ["one", "filter_dimensions_one"],
      ["two", "filter_dimensions_two"],
      ["three", "filter_dimensions_three"],
      ["four_or_more", "filter_dimensions_four_or_more"],
    ],
  },
  {
    name: "watchlists_by_keyword_count",
    exhaustive: true,
    buckets: [
      ["zero", "keyword_count_zero"],
      ["one", "keyword_count_one"],
      ["two_to_three", "keyword_count_two_to_three"],
      ["four_or_more", "keyword_count_four_or_more"],
    ],
  },
  {
    name: "watchlists_by_filter_presence",
    exhaustive: false,
    buckets: [
      ["location", "filter_presence_location"],
      ["occupation", "filter_presence_occupation"],
      ["seniority", "filter_presence_seniority"],
      ["technology", "filter_presence_technology"],
      ["work_mode", "filter_presence_work_mode"],
      ["employment_type", "filter_presence_employment_type"],
      ["salary", "filter_presence_salary"],
      ["experience", "filter_presence_experience"],
    ],
  },
] as const;

const POPULATION_COLUMN = "population_watchlists";

export const CENSUS_COUNT_COLUMNS = [
  POPULATION_COLUMN,
  ...CENSUS_METRICS.flatMap((metric) =>
    metric.buckets.map(([, column]) => column),
  ),
];

type QueryRow = Record<string, unknown>;

export type CensusQuery = (statement: string) => Promise<readonly QueryRow[]>;

export type CensusExecutor = {
  transaction: (
    work: (query: CensusQuery) => Promise<WatchlistFilterCensus>,
  ) => Promise<WatchlistFilterCensus>;
};

export type CensusRunContext = {
  database: string;
  role: string;
  approvalId: string;
  runId: string;
};

export type CensusCell = {
  bucket: string;
  count: number | null;
  suppressed: boolean;
};

export type WatchlistFilterCensus = {
  schemaVersion: typeof CENSUS_SCHEMA_VERSION;
  classificationSemantics: "aggregate_shape_approximation_v1";
  collectedAt: string;
  provenance: {
    approvalId: string;
    runId: string;
  };
  privacy: {
    aggregateOnly: true;
    minimumReleasedCellCount: typeof MINIMUM_RELEASED_CELL_COUNT;
    readOnlyTransactionVerified: true;
    populationTotalsReleased: false;
    complementarySuppression: true;
    readOnlyCredentialVerified: true;
  };
  metrics: Array<{
    name: string;
    buckets: CensusCell[];
  }>;
};

export class CensusGuardError extends Error {}

export function isValidCensusAuditIdentifier(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$/.test(value);
}

function parseCount(value: unknown): number {
  if (typeof value !== "string" || !/^(?:0|[1-9][0-9]*)$/.test(value)) {
    throw new CensusGuardError("Census returned an invalid aggregate count");
  }
  const count = Number(value);
  if (!Number.isSafeInteger(count)) {
    throw new CensusGuardError("Census returned an invalid aggregate count");
  }
  return count;
}

function assertOnlyExpectedColumns(
  row: QueryRow,
  expected: readonly string[],
): void {
  const actual = Object.keys(row).sort();
  const wanted = [...expected].sort();
  if (
    actual.length !== wanted.length ||
    actual.some((column, index) => column !== wanted[index])
  ) {
    throw new CensusGuardError("Census returned an unexpected aggregate shape");
  }
}

function metricKey(metricName: string, column: string): string {
  return `${metricName}:${column}`;
}

function addLowestCountsUntil(
  suppressed: Set<string>,
  metric: CensusMetricDefinition,
  counts: Readonly<Record<string, number>>,
  target: number,
): void {
  const existing = metric.buckets.filter(([, column]) =>
    suppressed.has(metricKey(metric.name, column)),
  ).length;
  if (existing >= target) return;

  const candidates = metric.buckets
    .filter(
      ([, column]) => !suppressed.has(metricKey(metric.name, column)),
    )
    .map(([, column], index) => ({ column, count: counts[column], index }))
    .sort((left, right) => left.count - right.count || left.index - right.index);

  for (const candidate of candidates.slice(0, target - existing)) {
    suppressed.add(metricKey(metric.name, candidate.column));
  }
}

function buildSuppressionSet(
  counts: Readonly<Record<string, number>>,
): Set<string> {
  const suppressed = new Set<string>();
  let sensitive = counts[POPULATION_COLUMN] < MINIMUM_RELEASED_CELL_COUNT;

  for (const metric of CENSUS_METRICS) {
    const population = counts[POPULATION_COLUMN];
    for (const [, column] of metric.buckets) {
      const count = counts[column];
      const complementIsSmall =
        !metric.exhaustive &&
        population - count < MINIMUM_RELEASED_CELL_COUNT;
      if (count < MINIMUM_RELEASED_CELL_COUNT || complementIsSmall) {
        suppressed.add(metricKey(metric.name, column));
        sensitive = true;
      }
    }
  }

  /*
   * A sensitive cell makes the shared population total sensitive too. Totals
   * are never emitted, and every same-universe view hides at least two cells,
   * so neither an exhaustive complement nor another marginal can reconstruct
   * a primary-suppressed count.
   */
  for (const metric of CENSUS_METRICS) {
    if (sensitive) {
      addLowestCountsUntil(suppressed, metric, counts, 2);
    }
  }
  return suppressed;
}

function assertAggregateInvariants(
  counts: Readonly<Record<string, number>>,
): void {
  const population = counts[POPULATION_COLUMN];
  for (const metric of CENSUS_METRICS) {
    const metricCounts = metric.buckets.map(([, column]) => counts[column]);
    if (metricCounts.some((count) => count > population)) {
      throw new CensusGuardError("Census returned inconsistent aggregate counts");
    }
    if (
      metric.exhaustive &&
      metricCounts.reduce((total, count) => total + count, 0) !== population
    ) {
      throw new CensusGuardError("Census returned inconsistent aggregate counts");
    }
  }
}

function assembleCensus(
  row: QueryRow,
  context: CensusRunContext,
  collectedAt: Date,
): WatchlistFilterCensus {
  assertOnlyExpectedColumns(row, CENSUS_COUNT_COLUMNS);
  const counts = Object.fromEntries(
    CENSUS_COUNT_COLUMNS.map((column) => [column, parseCount(row[column])]),
  );
  assertAggregateInvariants(counts);
  const suppressed = buildSuppressionSet(counts);

  return {
    schemaVersion: CENSUS_SCHEMA_VERSION,
    classificationSemantics: "aggregate_shape_approximation_v1",
    collectedAt: collectedAt.toISOString(),
    provenance: {
      approvalId: context.approvalId,
      runId: context.runId,
    },
    privacy: {
      aggregateOnly: true,
      minimumReleasedCellCount: MINIMUM_RELEASED_CELL_COUNT,
      readOnlyTransactionVerified: true,
      populationTotalsReleased: false,
      complementarySuppression: true,
      readOnlyCredentialVerified: true,
    },
    metrics: CENSUS_METRICS.map((metric) => ({
      name: metric.name,
      buckets: metric.buckets.map(([bucket, column]) => {
        const isSuppressed = suppressed.has(metricKey(metric.name, column));
        return {
          bucket,
          count: isSuppressed ? null : counts[column],
          suppressed: isSuppressed,
        };
      }),
    })),
  };
}

function verifyReadOnlyResult(
  rows: readonly QueryRow[],
  expected: CensusRunContext,
): void {
  if (rows.length !== 1) {
    throw new CensusGuardError("Could not verify the read-only transaction");
  }
  const row = rows[0];
  assertOnlyExpectedColumns(row, [
    "transaction_read_only",
    "default_transaction_read_only",
    "current_user",
    "current_database",
    "ssl",
  ]);
  if (
    row.transaction_read_only !== "on" ||
    row.default_transaction_read_only !== "on"
  ) {
    throw new CensusGuardError("Database transaction is not read-only by default");
  }
  if (row.ssl !== true) {
    throw new CensusGuardError("Database connection is not using TLS");
  }
  if (
    row.current_user !== expected.role ||
    row.current_database !== expected.database
  ) {
    throw new CensusGuardError("Database identity does not match approval");
  }
}

function verifyTablePrivileges(rows: readonly QueryRow[]): void {
  if (rows.length !== 1) {
    throw new CensusGuardError("Could not verify database table privileges");
  }
  const expectedColumns = [
    "role_superuser",
    "role_createdb",
    "role_createrole",
    "role_replication",
    "role_bypassrls",
    "database_create",
    "public_schema_create",
    "watchlist_insert",
    "watchlist_update",
    "watchlist_delete",
    "watchlist_truncate",
    "watchlist_trigger",
    "watchlist_references",
    "watchlist_company_insert",
    "watchlist_company_update",
    "watchlist_company_delete",
    "watchlist_company_truncate",
    "watchlist_company_trigger",
    "watchlist_company_references",
  ];
  const row = rows[0];
  assertOnlyExpectedColumns(row, expectedColumns);
  if (expectedColumns.some((column) => row[column] !== false)) {
    throw new CensusGuardError("Database role has write-capable privileges");
  }
}

export async function collectWatchlistFilterCensus(
  executor: CensusExecutor,
  context: CensusRunContext,
  collectedAt = new Date(),
): Promise<WatchlistFilterCensus> {
  for (const identifier of [context.approvalId, context.runId]) {
    if (!isValidCensusAuditIdentifier(identifier)) {
      throw new CensusGuardError("Census approval or run identifier is invalid");
    }
  }
  return executor.transaction(async (query) => {
    await query(READ_ONLY_TRANSACTION_SQL);
    verifyReadOnlyResult(await query(VERIFY_READ_ONLY_SQL), context);
    verifyTablePrivileges(await query(VERIFY_TABLE_PRIVILEGES_SQL));
    await query(CENSUS_STATEMENT_TIMEOUT_SQL);

    const rows = await query(WATCHLIST_FILTER_CENSUS_SQL);
    if (rows.length !== 1) {
      throw new CensusGuardError("Census did not return one aggregate row");
    }
    return assembleCensus(rows[0], context, collectedAt);
  });
}

export function serializeCensus(census: WatchlistFilterCensus): string {
  return `${JSON.stringify(census, null, 2)}\n`;
}
