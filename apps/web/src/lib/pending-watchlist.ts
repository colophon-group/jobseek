import type { SearchWatchlistDraft } from "@/lib/search/watchlist-draft";

const STORAGE_KEY = "jobseek:pending-watchlist:v1";
const MAX_AGE_MS = 2 * 60 * 60 * 1_000;
const MAX_RAW_LENGTH = 64 * 1_024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export const PENDING_WATCHLIST_LIMIT = 10;

export type PendingWatchlistIntent =
  | Readonly<{ kind: "create"; draft: SearchWatchlistDraft }>
  | Readonly<{ kind: "clone"; watchlistId: string; title?: string }>;

export type PendingWatchlistEntry = Readonly<{
  id: string;
  intent: PendingWatchlistIntent;
}>;

type StoredEntry = Readonly<{
  id: string;
  intent: PendingWatchlistIntent;
  storedAt: number;
}>;

type StoredCollection = Readonly<{
  entries: StoredEntry[];
  version: 2;
}>;

type LegacyStoredIntent = PendingWatchlistIntent & Readonly<{
  storedAt: number;
  version: 1;
}>;

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function validCreateDraft(value: unknown): value is SearchWatchlistDraft {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const draft = value as Partial<SearchWatchlistDraft>;
  return typeof draft.title === "string" &&
    draft.title.trim().length > 0 &&
    draft.title.length <= 100 &&
    (draft.description === undefined || (
      typeof draft.description === "string" && draft.description.length <= 1_000
    )) &&
    Array.isArray(draft.companyIds) &&
    draft.companyIds.length <= 25 &&
    draft.companyIds.every((id) => typeof id === "string" && UUID.test(id)) &&
    draft.filters != null &&
    typeof draft.filters === "object" &&
    !Array.isArray(draft.filters) &&
    draft.isPublic === false;
}

export function isPendingWatchlistIntent(value: unknown): value is PendingWatchlistIntent {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const intent = value as Partial<PendingWatchlistIntent>;
  if (intent.kind === "create") return validCreateDraft(intent.draft);
  return intent.kind === "clone" &&
    typeof intent.watchlistId === "string" &&
    UUID.test(intent.watchlistId) &&
    (intent.title === undefined || (
      typeof intent.title === "string" &&
      intent.title.trim().length > 0 &&
      intent.title.length <= 100
    ));
}

function validStoredAt(value: unknown, now: number): value is number {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    now - value <= MAX_AGE_MS &&
    value <= now + 60_000;
}

function createEntryId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    // The normal watchlist route accepts UUIDs only. Keep the compatibility
    // fallback route-safe for browsers that expose sessionStorage but not
    // `crypto.randomUUID()`.
    return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (digit) => (
      (Number(digit) ^ (Math.random() * 16 >> (Number(digit) / 4))).toString(16)
    ));
  }
}

function parse(raw: string | null): StoredEntry[] {
  if (!raw || raw.length > MAX_RAW_LENGTH) return [];
  try {
    const value = JSON.parse(raw) as Partial<StoredCollection | LegacyStoredIntent>;
    const now = Date.now();

    // Migrate the original single-intent shape without making an existing
    // anonymous watchlist disappear after this collection-based upgrade.
    const legacy = value as Partial<LegacyStoredIntent>;
    const legacyStoredAt = legacy.storedAt;
    if (legacy.version === 1 && validStoredAt(legacyStoredAt, now) && isPendingWatchlistIntent(legacy)) {
      return [{ id: createEntryId(), intent: legacy, storedAt: legacyStoredAt }];
    }

    if (value.version !== 2 || !Array.isArray(value.entries)) return [];
    return value.entries
      .filter((entry): entry is StoredEntry => (
        entry != null &&
        typeof entry === "object" &&
        typeof entry.id === "string" &&
        entry.id.length > 0 &&
        entry.id.length <= 100 &&
        validStoredAt(entry.storedAt, now) &&
        isPendingWatchlistIntent(entry.intent)
      ))
      .slice(0, PENDING_WATCHLIST_LIMIT);
  } catch {
    return [];
  }
}

function write(target: Storage, entries: StoredEntry[]): boolean {
  try {
    if (entries.length === 0) target.removeItem(STORAGE_KEY);
    else {
      target.setItem(STORAGE_KEY, JSON.stringify({
        entries,
        version: 2,
      } satisfies StoredCollection));
    }
    return true;
  } catch {
    return false;
  }
}

function cleanEntries(target: Storage): StoredEntry[] {
  const entries = parse(target.getItem(STORAGE_KEY));
  if (entries.length === 0) target.removeItem(STORAGE_KEY);
  else write(target, entries);
  return entries;
}

export function stagePendingWatchlistEntry(
  intent: PendingWatchlistIntent,
): PendingWatchlistEntry | null {
  const target = storage();
  if (!target || !isPendingWatchlistIntent(intent)) return null;
  const entries = cleanEntries(target);
  if (entries.length >= PENDING_WATCHLIST_LIMIT) return null;
  const entry = { id: createEntryId(), intent, storedAt: Date.now() };
  if (!write(target, [
    ...entries,
    entry,
  ])) return null;
  return { id: entry.id, intent: entry.intent };
}

export function stagePendingWatchlist(intent: PendingWatchlistIntent): boolean {
  return stagePendingWatchlistEntry(intent) != null;
}

export function readPendingWatchlists(): PendingWatchlistEntry[] {
  const target = storage();
  if (!target) return [];
  return cleanEntries(target).map(({ id, intent }) => ({ id, intent }));
}

export function takePendingWatchlists(): PendingWatchlistIntent[] {
  const target = storage();
  if (!target) return [];
  const entries = cleanEntries(target);
  target.removeItem(STORAGE_KEY);
  return entries.map(({ intent }) => intent);
}

export function removePendingWatchlist(id: string): void {
  const target = storage();
  if (!target) return;
  write(target, cleanEntries(target).filter((entry) => entry.id !== id));
}

export function updatePendingWatchlist(
  id: string,
  intent: PendingWatchlistIntent,
): boolean {
  const target = storage();
  if (!target || !isPendingWatchlistIntent(intent)) return false;
  const entries = cleanEntries(target);
  const index = entries.findIndex((entry) => entry.id === id);
  if (index === -1) return false;
  const next = [...entries];
  next[index] = { ...next[index], intent };
  return write(target, next);
}

// Singular helpers remain available for callers that only need the oldest
// pending item. New overview and handoff flows use the collection helpers.
export function readPendingWatchlist(): PendingWatchlistIntent | null {
  return readPendingWatchlists()[0]?.intent ?? null;
}

export function takePendingWatchlist(): PendingWatchlistIntent | null {
  const target = storage();
  if (!target) return null;
  const entries = cleanEntries(target);
  const [first, ...rest] = entries;
  write(target, rest);
  return first?.intent ?? null;
}

export function clearPendingWatchlist(): void {
  storage()?.removeItem(STORAGE_KEY);
}
