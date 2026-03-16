/**
 * @module linkedin-headcount-actor/snapshot
 *
 * Persistent headcount snapshot storage backed by Apify Key-Value Store.
 *
 * Purpose:
 *   The linkedin-headcount-actor detects *changes* in headcount over time.
 *   To do that, each run must compare the current scraped headcount against
 *   what was scraped in the previous run. This module stores and retrieves
 *   those point-in-time snapshots.
 *
 * Storage key format:
 *   `headcount_snapshot_{sanitized_company_name}`
 *   e.g. "headcount_snapshot_stripe_inc" for "Stripe, Inc."
 *
 * KV Store name: 'linkedin-headcount-snapshots'
 *   Opened in main.ts via: Actor.openKeyValueStore('linkedin-headcount-snapshots')
 *
 * First run behavior:
 *   loadSnapshot returns null → no signal emitted → baseline recorded.
 *   On the second run, delta is computed against the baseline.
 */

import { KeyValueStore } from 'apify';

/** A point-in-time snapshot of a company's LinkedIn headcount */
export interface HeadcountSnapshot {
  /** Company name as returned by the LinkedIn scraper */
  company: string;
  /** Total employee count (LinkedIn's reported figure) */
  headcount: number;
  /** List of office locations, e.g. ["San Francisco, CA", "London, UK"] */
  locations: string[];
  /** ISO 8601 timestamp of when this snapshot was taken */
  timestamp: string;
}

/** KV key prefix — all snapshot keys start with this string */
const KV_KEY_PREFIX = 'headcount_snapshot_';

/**
 * Normalizes a company name to a valid KV store key.
 * Converts to lowercase, replaces non-alphanumeric chars with underscores.
 * e.g. "Stripe, Inc." → "stripe__inc_"
 */
function sanitizeKey(company: string): string {
  return company
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '_')
    .slice(0, 200); // KV keys have a max length
}

/**
 * Loads the most recent headcount snapshot for a company from KV store.
 * Returns null if no snapshot exists (i.e., this is the first time we've seen this company).
 *
 * @param store   - Apify KeyValueStore instance (from Actor.openKeyValueStore)
 * @param company - Company name (will be sanitized to form the key)
 * @returns HeadcountSnapshot or null
 */
export async function loadSnapshot(
  store: KeyValueStore,
  company: string
): Promise<HeadcountSnapshot | null> {
  const key = `${KV_KEY_PREFIX}${sanitizeKey(company)}`;
  try {
    const value = await store.getValue<HeadcountSnapshot>(key);
    return value ?? null;
  } catch (err) {
    console.error(`Error loading snapshot for "${company}":`, err);
    return null;
  }
}

/**
 * Saves a headcount snapshot for a company to KV store.
 * Overwrites any previous snapshot — we only need the most recent baseline.
 *
 * @param store    - Apify KeyValueStore instance
 * @param company  - Company name (will be sanitized to form the key)
 * @param snapshot - Snapshot data to persist
 * @throws Re-throws KV store errors so the caller can decide how to handle them
 */
export async function saveSnapshot(
  store: KeyValueStore,
  company: string,
  snapshot: HeadcountSnapshot
): Promise<void> {
  const key = `${KV_KEY_PREFIX}${sanitizeKey(company)}`;
  try {
    await store.setValue(key, snapshot);
  } catch (err) {
    console.error(`Error saving snapshot for "${company}":`, err);
    throw err;
  }
}
