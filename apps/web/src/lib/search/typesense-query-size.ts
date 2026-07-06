export type TypesenseQueryParamValue = string | number | boolean | null | undefined;

export type TypesenseQueryParams = Record<string, TypesenseQueryParamValue>;

// Typesense rejects GET search query strings above 4000 bytes.
// Keep headroom for SDK/browser serialization details and future params.
export const TYPESENSE_SAFE_QUERY_STRING_LENGTH = 3500;

export function typesenseQueryStringLength(params: TypesenseQueryParams): number {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    query.set(key, String(value));
  }
  return query.toString().length;
}

export function isTypesenseQueryStringSafe(params: TypesenseQueryParams): boolean {
  return typesenseQueryStringLength(params) <= TYPESENSE_SAFE_QUERY_STRING_LENGTH;
}

export function splitValuesForTypesenseQuery(
  values: readonly string[],
  buildParams: (batch: readonly string[]) => TypesenseQueryParams,
  maxValuesPerBatch: number,
): string[][] {
  const batches: string[][] = [];
  let batch: string[] = [];

  for (const value of values) {
    if (batch.length >= maxValuesPerBatch) {
      batches.push(batch);
      batch = [];
    }

    const candidate = [...batch, value];
    if (batch.length > 0 && !isTypesenseQueryStringSafe(buildParams(candidate))) {
      batches.push(batch);
      batch = [value];
    } else {
      batch = candidate;
    }
  }

  if (batch.length > 0) batches.push(batch);
  return batches;
}
