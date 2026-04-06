import { Client } from "typesense";
import type { ConfigurationOptions } from "typesense/lib/Typesense/Configuration";

/**
 * Typesense client singletons.
 *
 * Uses Next.js global singleton pattern to survive dev-mode module re-execution.
 * Two clients with different API keys:
 *   - search client: read-only (TYPESENSE_SEARCH_KEY)
 *   - write client: write access for watchlist mutations (TYPESENSE_WRITE_KEY)
 */

function createClient(apiKey: string): Client {
  const host = process.env.TYPESENSE_HOST;
  const port = process.env.TYPESENSE_PORT;
  const protocol = process.env.TYPESENSE_PROTOCOL;

  if (!host || !port || !protocol) {
    throw new Error(
      `Typesense connection not configured. Missing: ${[
        !host && "TYPESENSE_HOST",
        !port && "TYPESENSE_PORT",
        !protocol && "TYPESENSE_PROTOCOL",
      ]
        .filter(Boolean)
        .join(", ")}`,
    );
  }

  const config: ConfigurationOptions = {
    nodes: [{ host, port: parseInt(port, 10), protocol }],
    apiKey,
    connectionTimeoutSeconds: 5,
  };

  return new Client(config);
}

const globalForTypesense = globalThis as unknown as {
  __typesenseSearchClient?: Client;
  __typesenseWriteClient?: Client;
};

export function getSearchClient(): Client {
  if (!globalForTypesense.__typesenseSearchClient) {
    const key = process.env.TYPESENSE_SEARCH_KEY;
    if (!key) throw new Error("TYPESENSE_SEARCH_KEY is not set");
    globalForTypesense.__typesenseSearchClient = createClient(key);
  }
  return globalForTypesense.__typesenseSearchClient;
}

/** Alias for getSearchClient — used by typeahead/browse-all functions. */
export const getTypesenseClient = getSearchClient;

/** Hit type from Typesense search response. */
export interface TypesenseHit {
  document: Record<string, unknown>;
  highlights?: Array<{
    field: string;
    snippet?: string;
    snippets?: string[];
    value?: string;
    matched_tokens?: string[] | string[][];
  }>;
  text_match?: number;
}

/** Typed search result wrapper. */
export interface TypesenseSearchResult {
  found: number;
  hits?: TypesenseHit[];
  grouped_hits?: Array<{
    group_key: string[];
    hits: TypesenseHit[];
    found: number;
  }>;
  facet_counts?: Array<{
    field_name: string;
    counts: Array<{ value: string; count: number }>;
    stats: { total_values?: number };
  }>;
  search_time_ms?: number;
}

export function getWriteClient(): Client | null {
  if (!globalForTypesense.__typesenseWriteClient) {
    const key = process.env.TYPESENSE_WRITE_KEY;
    if (!key) return null;
    globalForTypesense.__typesenseWriteClient = createClient(key);
  }
  return globalForTypesense.__typesenseWriteClient;
}
