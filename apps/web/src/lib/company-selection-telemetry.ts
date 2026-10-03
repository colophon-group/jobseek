/** Bounded outcome counters for selection writes; never log arguments or errors. */
type SelectionOperation = "create" | "handoff" | "update" | "copy" | "copy_shared" | "add" | "remove" | "clear" | "star";
const REJECTIONS = new Map([
  ["invalid_company", "invalid_input"], ["invalid_companies", "invalid_input"],
  ["invalid_input", "invalid_input"], ["invalid_source", "invalid_input"],
  ["unknown_company", "lookup_miss"], ["company_lookup_unavailable", "lookup_unavailable"],
  ["company_identity_conflict", "identity_conflict"], ["not_found", "not_found_or_forbidden"],
  ["limit_reached", "limit"], ["company_limit_reached", "limit"],
  ["source_too_large", "limit"], ["too_large", "limit"], ["rate_limited", "rate_limited"],
]);

function failureOutcomeUnsafe(error: unknown): string {
  if (error instanceof Error && error.message === "Not authenticated") return "unauthenticated";
  let current = error;
  const seen = new Set<object>();
  for (let depth = 0; depth < 5; depth++) {
    if (!current || typeof current !== "object" || seen.has(current)) break;
    seen.add(current);
    const { code, cause } = current as { code?: unknown; cause?: unknown };
    if (code === "23503" || code === "23001") return "database_foreign_key";
    if (typeof code === "string" && /^[0-9A-Z]{5}$/.test(code)) return "database_error";
    current = cause;
  }
  return "unexpected_failure";
}

function failureOutcome(error: unknown): string {
  try { return failureOutcomeUnsafe(error); } catch { return "unexpected_failure"; }
}

export async function observeCompanySelection<T>(operation: SelectionOperation, run: () => Promise<T>): Promise<T> {
  let outcome = "unexpected_failure";
  try {
    const result = await run();
    try {
      const value = result as { error?: unknown; ok?: unknown } | null;
      outcome = value && typeof value === "object" && typeof value.error === "string"
        ? REJECTIONS.get(value.error) ?? "rejected"
        : value && typeof value === "object" && value.ok === false ? "rejected" : "success";
    } catch {
      // An exotic result getter must not change the successful mutation.
    }
    return result;
  } catch (error) {
    outcome = failureOutcome(error);
    throw error;
  } finally {
    try {
      console.info(JSON.stringify({ event: "company_selection_mutation", operation, outcome }));
    } catch {
      // Logging failure must never turn a committed mutation into a retry.
    }
  }
}
