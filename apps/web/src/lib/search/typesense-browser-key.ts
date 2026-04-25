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
const BROADCAST_CHANNEL_NAME = "typesense-key-clear";

let broadcastChannel: BroadcastChannel | null = null;

function ensureBroadcastListener(): void {
  if (typeof BroadcastChannel === "undefined") return;
  if (broadcastChannel) return;
  broadcastChannel = new BroadcastChannel(BROADCAST_CHANNEL_NAME);
  broadcastChannel.onmessage = () => {
    cached = null;
    inflight = null;
  };
}

async function fetchKey(): Promise<TypesenseBrowserConfig> {
  const res = await fetch("/api/typesense-key", { credentials: "same-origin" });
  if (!res.ok) throw new Error(`typesense-key endpoint returned ${res.status}`);
  return res.json();
}

export async function getTypesenseBrowserConfig(): Promise<TypesenseBrowserConfig> {
  ensureBroadcastListener();
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

/**
 * Clears the cached scoped key in this tab AND broadcasts to other tabs so
 * sign-in/out in tab A immediately invalidates the stale key in tab B.
 */
export function clearTypesenseBrowserConfig(): void {
  cached = null;
  inflight = null;
  if (typeof BroadcastChannel === "undefined") return;
  // Use a dedicated transient channel for the post — listeners on the
  // long-lived channel pick it up. Closing immediately keeps GC tidy.
  try {
    const ch = new BroadcastChannel(BROADCAST_CHANNEL_NAME);
    ch.postMessage({ type: "clear" });
    ch.close();
  } catch {
    // BroadcastChannel can throw in private-mode browsers; safe to ignore.
  }
}
