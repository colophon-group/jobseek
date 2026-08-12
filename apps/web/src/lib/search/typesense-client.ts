import { Client } from "typesense";
import type { ConfigurationOptions } from "typesense/lib/Typesense/Configuration";
import {
  sanitizeTypesenseBoundaryError,
  withSanitizedTypesenseBoundary,
} from "@/lib/search/typesense-retry";

/**
 * Typesense client singletons.
 *
 * Uses Next.js global singleton pattern to survive dev-mode module re-execution.
 * Two clients with different API keys:
 *   - search client: read-only (TYPESENSE_SEARCH_KEY)
 *   - write client: write access for watchlist mutations (TYPESENSE_WRITE_KEY)
 */

function isObjectLike(value: unknown): value is object | ((...args: never[]) => unknown) {
  return (typeof value === "object" && value !== null) || typeof value === "function";
}

/**
 * Recursively proxy the SDK's fluent resource objects. Every terminal request
 * promise is caught before an Axios/Typesense error can reach a Next cache or
 * Server Action. Method receivers remain the original SDK instances so
 * classes using private state continue to work normally.
 */
export function sanitizeTypesenseClientBoundary<T extends object>(client: T): T {
  const proxies = new WeakMap<object, object>();

  const wrap = (target: object): object => {
    const cached = proxies.get(target);
    if (cached) return cached;

    const proxy = new Proxy(target, {
      get(innerTarget, property) {
        try {
          const value = Reflect.get(innerTarget, property, innerTarget);
          if (typeof value !== "function") {
            return isObjectLike(value) ? wrap(value) : value;
          }

          return (...args: unknown[]) => {
            try {
              const result = Reflect.apply(value, innerTarget, args);
              if (!isObjectLike(result)) return result;

              let then: unknown;
              try {
                then = Reflect.get(result, "then");
              } catch (err) {
                throw sanitizeTypesenseBoundaryError(err);
              }
              if (typeof then === "function") {
                return withSanitizedTypesenseBoundary(
                  () => result as PromiseLike<unknown>,
                );
              }
              return wrap(result);
            } catch (err) {
              throw sanitizeTypesenseBoundaryError(err);
            }
          };
        } catch (err) {
          throw sanitizeTypesenseBoundaryError(err);
        }
      },
    });
    proxies.set(target, proxy);
    return proxy;
  };

  return wrap(client) as T;
}

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
    // The SDK's default warning interpolates upstream error messages and
    // response bodies. Application call sites emit a deliberately lossy,
    // structured envelope instead; keep credentialed SDK internals silent.
    logLevel: "silent",
  };

  try {
    return sanitizeTypesenseClientBoundary(new Client(config));
  } catch (err) {
    throw sanitizeTypesenseBoundaryError(err);
  }
}

const globalForTypesense = globalThis as unknown as {
  __typesenseSearchClient?: Client;
  __typesenseWriteClient?: Client;
};

export function getSearchClient(): Client {
  if (!globalForTypesense.__typesenseSearchClient) {
    const key = process.env.TYPESENSE_SEARCH_KEY;
    if (!key) {
      throw sanitizeTypesenseBoundaryError(
        new Error("TYPESENSE_SEARCH_KEY is not set"),
      );
    }
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
