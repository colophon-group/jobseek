import { headers } from "next/headers";

/** Pick the first value when a search param appears multiple times (?q=a&q=b). */
export function firstOf(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** Return array of IDs, or undefined if empty (DB queries treat undefined as "no filter"). */
export function idsOrUndefined(items: { id: number }[]): number[] | undefined {
  return items.length > 0 ? items.map((i) => i.id) : undefined;
}

/** Parse a "min-max" range param like sal=50000-120000 or exp=3-10. */
export function parseRangeParam(val: string | undefined): { min: number | undefined; max: number | undefined } {
  if (!val) return { min: undefined, max: undefined };
  const [minStr, maxStr] = val.split("-");
  return {
    min: minStr ? parseInt(minStr, 10) : undefined,
    max: maxStr ? parseInt(maxStr, 10) : undefined,
  };
}

/** Read Vercel geolocation headers, returning undefined for missing/invalid values. */
export async function getGeoFromHeaders(): Promise<{ userLat: number | undefined; userLng: number | undefined }> {
  const h = await headers();
  const lat = parseFloat(h.get("x-vercel-ip-latitude") ?? "");
  const lng = parseFloat(h.get("x-vercel-ip-longitude") ?? "");
  return {
    userLat: Number.isFinite(lat) ? lat : undefined,
    userLng: Number.isFinite(lng) ? lng : undefined,
  };
}
