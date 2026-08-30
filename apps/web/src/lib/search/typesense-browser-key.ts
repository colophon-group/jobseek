export interface TypesenseBrowserConfig {
  apiKey: string;
  host: string;
  port: number;
  protocol: string;
  expiresAt: number;
}

let cached: TypesenseBrowserConfig | null = null;
let inflight: Promise<TypesenseBrowserConfig> | null = null;

const REFRESH_LEAD_MS = 30_000;
const STORAGE_KEY = "typesense-browser-config-v1";

function isUsableConfig(value: unknown): value is TypesenseBrowserConfig {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<TypesenseBrowserConfig>;
  return (
    typeof candidate.apiKey === "string" &&
    candidate.apiKey.length > 0 &&
    typeof candidate.host === "string" &&
    candidate.host.length > 0 &&
    typeof candidate.port === "number" &&
    Number.isInteger(candidate.port) &&
    candidate.port > 0 &&
    typeof candidate.protocol === "string" &&
    candidate.protocol.length > 0 &&
    typeof candidate.expiresAt === "number" &&
    candidate.expiresAt - Date.now() > REFRESH_LEAD_MS
  );
}

function loadPersistedConfig(): TypesenseBrowserConfig | null {
  if (typeof localStorage === "undefined") return null;
  try {
    const serialized = localStorage.getItem(STORAGE_KEY);
    if (!serialized) return null;
    const value: unknown = JSON.parse(serialized);
    if (isUsableConfig(value)) return value;
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage can be unavailable in private mode or contain malformed data.
  }
  return null;
}

function persistConfig(config: TypesenseBrowserConfig): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  } catch {
    // In-memory caching still works when storage is unavailable or full.
  }
}

async function fetchKey(): Promise<TypesenseBrowserConfig> {
  const res = await fetch("/api/typesense-key", { credentials: "same-origin" });
  if (!res.ok) throw new Error(`typesense-key endpoint returned ${res.status}`);
  return res.json();
}

export async function getTypesenseBrowserConfig(): Promise<TypesenseBrowserConfig> {
  if (isUsableConfig(cached)) return cached;

  const persisted = loadPersistedConfig();
  if (persisted) {
    cached = persisted;
    return persisted;
  }

  if (!inflight) {
    inflight = fetchKey()
      .then((cfg) => {
        if (!isUsableConfig(cfg)) {
          throw new Error("typesense-key endpoint returned an expired config");
        }
        cached = cfg;
        persistConfig(cfg);
        return cfg;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** Clears both memory and persisted state after an explicit config reset. */
export function clearTypesenseBrowserConfig(): void {
  cached = null;
  inflight = null;
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage may be unavailable in private mode; memory is already clear.
  }
}

/**
 * A rotated/revoked parent invalidates every child derived from it. Drop a
 * persisted child after Typesense reports that it is unauthorized so the next
 * browser operation obtains the replacement instead of reusing it until TTL.
 */
export function invalidateTypesenseBrowserConfigIfUnauthorized(status: number): void {
  if (status === 401) clearTypesenseBrowserConfig();
}
