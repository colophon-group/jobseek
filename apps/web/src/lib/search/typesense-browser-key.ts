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

async function fetchKey(): Promise<TypesenseBrowserConfig> {
  const res = await fetch("/api/typesense-key", { credentials: "same-origin" });
  if (!res.ok) throw new Error(`typesense-key endpoint returned ${res.status}`);
  return res.json();
}

export async function getTypesenseBrowserConfig(): Promise<TypesenseBrowserConfig> {
  if (cached && cached.expiresAt - Date.now() > REFRESH_LEAD_MS) return cached;
  if (!inflight) {
    inflight = fetchKey()
      .then((cfg) => {
        cached = cfg;
        return cfg;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

export function clearTypesenseBrowserConfig(): void {
  cached = null;
  inflight = null;
}
