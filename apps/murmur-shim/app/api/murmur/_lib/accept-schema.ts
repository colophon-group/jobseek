/**
 * Vendored schema for the `final_output` payload Murmur POSTs to
 * `/api/murmur/accept`.
 *
 * Source of truth: `apps/crawler/murmur/pipelines/add-company.yaml`
 * (`final_output.composes`). The composed shape combines fields from
 * `pre-verify`, `list-boards.boards`, and per-board `configure-board.*`
 * outputs into a single object the publisher's accept handler ingests.
 *
 * The schema dialect here is a small superset of the J5 dialect
 * (`schemas.ts`): we keep `type: object` with required + properties,
 * but add an `array` type with an `items` schema and `minItems`. This is
 * the smallest extension that lets us express the boards list / kb /
 * industry_ids without pulling in a real JSON-Schema library.
 *
 * @see colophon-group/jobseek#2763
 * @see Murmur DESIGN.md §4.1 (Webhook accept-handler contract)
 */

/** Per-board entry composed from `list-boards × configure-board.*`. */
export interface FinalOutputBoard {
  readonly alias: string;
  readonly board_url: string;
  readonly provider: string;
  readonly hreflang?: string;
  readonly outcome: "configured" | "blocked";
  readonly monitor_type: string;
  readonly monitor_config: Record<string, unknown>;
  readonly scraper_type: string;
  readonly scraper_config: Record<string, unknown>;
  readonly verdict: "ok" | "needs-work" | "rejected";
  readonly per_field?: Record<string, unknown>;
}

/** Full composed `final_output` payload the webhook receives. */
export interface FinalOutput {
  readonly canonical_name: string;
  readonly canonical_website: string;
  readonly slug: string;
  readonly description: string;
  readonly industry_ids: readonly string[];
  readonly boards: readonly FinalOutputBoard[];
  readonly kb_entries?: readonly Record<string, unknown>[];
  readonly case_studies?: readonly Record<string, unknown>[];
}

/**
 * Property schema for the accept dialect. The shape mirrors the J5
 * `SubcommandPropertySchema` and adds:
 *   - `type: "array"` with `items` (a nested property schema) and
 *     optional `minItems` / `maxItems`.
 *   - `maxLength` on strings.
 *   - inline nested object schemas under `properties.<key>` recursion.
 *
 * Anything outside this subset is a hard validation error in
 * `validateAcceptBody`.
 */
export type AcceptPropertySchema =
  | {
      readonly type: "string";
      readonly format?: "uri";
      readonly pattern?: string;
      readonly enum?: readonly string[];
      readonly minLength?: number;
      readonly maxLength?: number;
    }
  | {
      readonly type: "integer" | "number";
      readonly minimum?: number;
    }
  | { readonly type: "boolean" }
  | {
      readonly type: "object";
      readonly additionalProperties?: boolean;
      readonly required?: readonly string[];
      readonly properties?: Readonly<Record<string, AcceptPropertySchema>>;
    }
  | {
      readonly type: "array";
      readonly items: AcceptPropertySchema;
      readonly minItems?: number;
      readonly maxItems?: number;
    };

/** Top-level object schema for an accept-shaped body. */
export interface AcceptObjectSchema {
  readonly type: "object";
  readonly additionalProperties: false;
  readonly required: readonly string[];
  readonly properties: Readonly<Record<string, AcceptPropertySchema>>;
}

/**
 * Schema for one entry inside `final_output.boards` — the
 * `list-boards.boards[*]` item composed with the spawned
 * `configure-board.*` output.
 */
export const FINAL_OUTPUT_BOARD_SCHEMA: AcceptObjectSchema = {
  type: "object",
  additionalProperties: false,
  required: [
    "alias",
    "board_url",
    "provider",
    "outcome",
    "monitor_type",
    "monitor_config",
    "scraper_type",
    "scraper_config",
    "verdict",
  ],
  properties: {
    alias: { type: "string", minLength: 1 },
    board_url: { type: "string", format: "uri", pattern: "^https://" },
    provider: { type: "string", minLength: 1 },
    hreflang: { type: "string" },
    outcome: { type: "string", enum: ["configured", "blocked"] },
    monitor_type: { type: "string", minLength: 1 },
    monitor_config: { type: "object" },
    scraper_type: { type: "string", minLength: 1 },
    scraper_config: { type: "object" },
    verdict: { type: "string", enum: ["ok", "needs-work", "rejected"] },
    per_field: { type: "object" },
  },
} as const;

/** Top-level `final_output` schema. */
export const FINAL_OUTPUT_SCHEMA: AcceptObjectSchema = {
  type: "object",
  additionalProperties: false,
  required: [
    "canonical_name",
    "canonical_website",
    "slug",
    "description",
    "industry_ids",
    "boards",
  ],
  properties: {
    canonical_name: { type: "string", minLength: 1 },
    canonical_website: {
      type: "string",
      format: "uri",
      pattern: "^https://",
    },
    slug: { type: "string", pattern: "^[a-z][a-z0-9-]*[a-z0-9]$" },
    description: { type: "string", minLength: 1, maxLength: 400 },
    industry_ids: {
      type: "array",
      minItems: 1,
      maxItems: 4,
      items: { type: "string", pattern: "^[a-z][a-z0-9-]*[a-z0-9]$" },
    },
    boards: {
      type: "array",
      minItems: 1,
      items: {
        type: "object",
        additionalProperties: false,
        required: FINAL_OUTPUT_BOARD_SCHEMA.required,
        properties: FINAL_OUTPUT_BOARD_SCHEMA.properties,
      },
    },
    kb_entries: { type: "array", items: { type: "object" } },
    case_studies: { type: "array", items: { type: "object" } },
  },
} as const;
