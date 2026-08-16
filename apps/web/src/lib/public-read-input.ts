const DEFAULT_MAX_LIMIT = 100;
const DEFAULT_MAX_OFFSET = 5_000;

/**
 * Reject pagination amplification supplied through a replayed Server Action.
 * UI callers use page sizes of 10-20, while 100 leaves room for legitimate
 * internal/admin clients without allowing a single action to request an
 * unbounded Typesense page or facet window.
 */
export function assertBoundedPublicPagination(
  value: unknown,
  options: { maxLimit?: number; maxOffset?: number } = {},
): void {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Invalid public read parameters");
  }

  const params = value as { offset?: unknown; limit?: unknown };
  const maxLimit = options.maxLimit ?? DEFAULT_MAX_LIMIT;
  const maxOffset = options.maxOffset ?? DEFAULT_MAX_OFFSET;

  if (
    params.offset !== undefined &&
    (!Number.isSafeInteger(params.offset) ||
      (params.offset as number) < 0 ||
      (params.offset as number) > maxOffset)
  ) {
    throw new Error("Invalid public read offset");
  }
  if (
    params.limit !== undefined &&
    (!Number.isSafeInteger(params.limit) ||
      (params.limit as number) < 1 ||
      (params.limit as number) > maxLimit)
  ) {
    throw new Error("Invalid public read limit");
  }
}
