export type TypesenseMultiSearchResult<T> = {
  found: number;
  hits?: Array<{
    document: T;
    text_match?: number;
  }>;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function malformedBatch(): Error {
  // Keep upstream response content out of errors that can cross a Server
  // Action boundary or reach client-side telemetry.
  return new Error("Typesense multi_search response was malformed");
}

/**
 * Validate the ordered result envelope returned by Typesense multi_search.
 * A missing slot or a successful HTTP response containing a per-search error
 * fails the whole batch, so callers can never pair postings with the wrong
 * active/year count.
 */
export function parseTypesenseMultiSearchResults<T>(
  value: unknown,
  expectedResults: number,
  options: { expectHitsAt?: readonly number[] } = {},
): TypesenseMultiSearchResult<T>[] {
  if (!isRecord(value) || !Array.isArray(value.results)) {
    throw malformedBatch();
  }
  if (value.results.length !== expectedResults) {
    throw malformedBatch();
  }

  const expectHits = new Set(options.expectHitsAt ?? []);
  return value.results.map((rawResult, index) => {
    if (!isRecord(rawResult) || rawResult.error !== undefined) {
      throw malformedBatch();
    }
    if (
      typeof rawResult.found !== "number" ||
      !Number.isInteger(rawResult.found) ||
      rawResult.found < 0
    ) {
      throw malformedBatch();
    }
    if (rawResult.hits !== undefined && !Array.isArray(rawResult.hits)) {
      throw malformedBatch();
    }
    if (expectHits.has(index) && rawResult.found > 0 && !Array.isArray(rawResult.hits)) {
      throw malformedBatch();
    }
    if (
      Array.isArray(rawResult.hits) &&
      rawResult.hits.some((hit) => {
        if (!isRecord(hit) || !isRecord(hit.document)) return true;
        return (
          hit.text_match !== undefined &&
          (typeof hit.text_match !== "number" || !Number.isFinite(hit.text_match))
        );
      })
    ) {
      throw malformedBatch();
    }
    return rawResult as unknown as TypesenseMultiSearchResult<T>;
  });
}

