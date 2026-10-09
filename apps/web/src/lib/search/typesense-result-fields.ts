/** Fields consumed by posting mapping and canonical-company fallback. */
export const POSTING_RESULT_FIELDS = [
  "id", "company_id", "company_name", "company_slug", "company_icon", "title",
  "is_active", "location_ids", "location_names", "location_types",
  "location_geo_types", "first_seen_at",
].join(",");

// Providers return plain titles and numeric relevance; highlighting is unused.
// Keep this away from taxonomy suggesters, which use highlighted aliases.
export const POSTING_RESULT_PARAMETERS = {
  include_fields: POSTING_RESULT_FIELDS,
  highlight_fields: "none",
  highlight_full_fields: "none",
} as const;
