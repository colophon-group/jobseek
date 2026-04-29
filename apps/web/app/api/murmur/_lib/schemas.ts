/**
 * Vendored input_schemas for the seven Murmur subcommands jobseek serves.
 *
 * Source of truth: `apps/crawler/murmur/pipelines/add-company.yaml` —
 * `subtasks[id=configure-board].subcommands[*].input_schema`. The schemas
 * are duplicated here as TS constants so route handlers can validate
 * synchronously without parsing YAML on every request.
 *
 * Whenever the YAML schemas change, mirror the change here. The
 * `apps/web/__tests__/murmur-schemas-match-yaml.test.ts` (added under J5)
 * does NOT exist — there is no automated drift gate. Reviewers should
 * cross-check by hand on schema changes.
 *
 * The schema dialect is the demo-minimum subset declared in the YAML:
 *
 *   - `type: object`
 *   - `additionalProperties: false`
 *   - `required: [...]`
 *   - per-property: `type`, `format: uri`, `pattern`, `enum`, `minLength`,
 *     `minimum`
 *
 * Anything outside that subset must be rejected at validation time so we
 * fail loudly rather than silently accept.
 *
 * @see colophon-group/jobseek#2759
 */

/**
 * Minimal JSON-Schema-like shape we accept. Intentionally narrow.
 * `format` is recognised only when set to `"uri"` — that's the only
 * format the YAML uses today.
 */
export interface SubcommandSchema {
  readonly type: "object";
  readonly additionalProperties: false;
  readonly required: readonly string[];
  readonly properties: Readonly<Record<string, SubcommandPropertySchema>>;
}

export interface SubcommandPropertySchema {
  readonly type: "string" | "integer" | "number" | "object" | "boolean";
  readonly format?: "uri";
  readonly pattern?: string;
  readonly enum?: readonly string[];
  readonly minLength?: number;
  readonly minimum?: number;
}

// ── Subcommand schemas ─────────────────────────────────────────────

/** `probe monitor` — POST /api/murmur/probes/monitor */
export const PROBE_MONITOR_SCHEMA: SubcommandSchema = {
  type: "object",
  additionalProperties: false,
  required: ["board_url"],
  properties: {
    board_url: { type: "string", format: "uri", pattern: "^https://" },
    hreflang: { type: "string" },
    expected_count: { type: "integer", minimum: 0 },
  },
} as const;

/** `select monitor` — POST /api/murmur/select/monitor */
export const SELECT_MONITOR_SCHEMA: SubcommandSchema = {
  type: "object",
  additionalProperties: false,
  required: ["candidate_id", "board_url"],
  properties: {
    candidate_id: { type: "string", minLength: 1 },
    board_url: { type: "string", format: "uri", pattern: "^https://" },
  },
} as const;

/** `run monitor` — POST /api/murmur/run/monitor */
export const RUN_MONITOR_SCHEMA: SubcommandSchema = {
  type: "object",
  additionalProperties: false,
  required: ["board_url"],
  properties: {
    board_url: { type: "string", format: "uri", pattern: "^https://" },
  },
} as const;

/** `probe scraper` — POST /api/murmur/probes/scraper */
export const PROBE_SCRAPER_SCHEMA: SubcommandSchema = {
  type: "object",
  additionalProperties: false,
  required: ["board_url", "monitor_type", "monitor_config"],
  properties: {
    board_url: { type: "string", format: "uri", pattern: "^https://" },
    monitor_type: { type: "string", minLength: 1 },
    monitor_config: { type: "object" },
  },
} as const;

/** `select scraper` — POST /api/murmur/select/scraper */
export const SELECT_SCRAPER_SCHEMA: SubcommandSchema = {
  type: "object",
  additionalProperties: false,
  required: ["candidate_id", "board_url"],
  properties: {
    candidate_id: { type: "string", minLength: 1 },
    board_url: { type: "string", format: "uri", pattern: "^https://" },
  },
} as const;

/** `run scraper` — POST /api/murmur/run/scraper */
export const RUN_SCRAPER_SCHEMA: SubcommandSchema = {
  type: "object",
  additionalProperties: false,
  required: ["board_url"],
  properties: {
    board_url: { type: "string", format: "uri", pattern: "^https://" },
    sample_job_url: { type: "string", format: "uri" },
  },
} as const;

/** `feedback` — POST /api/murmur/feedback */
export const FEEDBACK_SCHEMA: SubcommandSchema = {
  type: "object",
  additionalProperties: false,
  required: ["verdict"],
  properties: {
    verdict: { type: "string", enum: ["ok", "needs-work", "rejected"] },
    kind: { type: "string" },
    per_field: { type: "object" },
    notes: { type: "string" },
  },
} as const;
