"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { QueryIntentProposal } from "@/lib/search/query-intent";

const IDLE_MS = 900;
const CLIENT_DEADLINE_MS = 1_800;
const ROLLOUT_STORAGE_KEY = "search-query-jev-rollout-v1";
let fallbackBucket: number | null = null;

function inJevRollout(): boolean {
  if (process.env.NEXT_PUBLIC_SEARCH_QUERY_JEV_ENABLED !== "true") return false;
  const configured = process.env.NEXT_PUBLIC_SEARCH_QUERY_JEV_ROLLOUT_PERCENT;
  const percent = configured === undefined ? 100 : Number(configured);
  if (!Number.isInteger(percent) || percent < 0 || percent > 100) return false;
  if (percent === 100) return true;
  if (percent === 0) return false;

  let bucket = fallbackBucket;
  try {
    const saved = window.localStorage.getItem(ROLLOUT_STORAGE_KEY);
    const parsed = saved === null ? NaN : Number(saved);
    if (Number.isInteger(parsed) && parsed >= 0 && parsed < 10_000) {
      bucket = parsed;
    } else {
      bucket ??= Math.floor(Math.random() * 10_000);
      window.localStorage.setItem(ROLLOUT_STORAGE_KEY, String(bucket));
    }
  } catch {
    bucket ??= Math.floor(Math.random() * 10_000);
  }
  fallbackBucket = bucket;
  return bucket < percent * 100;
}

/** Paid routing only begins once there is more than an elementary term. */
export function canRouteQuery(query: string, unresolvedAtomic = false): boolean {
  const words = query.trim().split(/\s+/).filter(Boolean);
  return inJevRollout() &&
    query.length <= 180 && words.length <= 14 &&
    (words.length >= 2 || (unresolvedAtomic && words.length === 1 && query.trim().length >= 4));
}

export function useSearchBarQueryIntent(
  locale: string, contextKey: string, onReady: () => void,
  isUnresolvedAtomic: (query: string) => boolean = () => false,
) {
  const [proposal, setProposal] = useState<QueryIntentProposal | null>(null);
  const ready = useRef<QueryIntentProposal | null>(null);
  const [pending, setPending] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const request = useRef<{ query: string; promise: Promise<QueryIntentProposal | null>; controller: AbortController } | null>(null);
  const attempted = useRef<string | null>(null);
  const generation = useRef(0);

  const clear = useCallback(() => {
    generation.current += 1;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    request.current?.controller.abort();
    request.current = null;
    attempted.current = null;
    ready.current = null;
    setProposal(null);
    setPending(false);
  }, []);

  const requestNow = useCallback((query: string): Promise<QueryIntentProposal | null> => {
    const normalized = query.trim();
    if (!canRouteQuery(normalized, isUnresolvedAtomic(normalized))) return Promise.resolve(null);
    if (ready.current?.query === normalized && ready.current.locale === locale) return Promise.resolve(ready.current);
    if (request.current?.query === normalized) return request.current.promise;
    if (attempted.current === `${locale}:${normalized}`) return Promise.resolve(null);
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    request.current?.controller.abort();
    attempted.current = `${locale}:${normalized}`;
    const controller = new AbortController();
    const owner = generation.current;
    setPending(true);
    const promise = fetch("/api/search/query-intent", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query: normalized, locale }),
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(CLIENT_DEADLINE_MS)]),
      cache: "no-store",
    }).then(async (response) => {
      if (!response.ok) return null;
      const value: unknown = await response.json();
      if (!value || typeof value !== "object") return null;
      const result = value as QueryIntentProposal;
      if (result.query !== normalized || result.locale !== locale ||
        !Array.isArray(result.keywords) || !Array.isArray(result.locations) ||
        !Array.isArray(result.occupations) || !Array.isArray(result.seniorities) ||
        !Array.isArray(result.technologies) || !Array.isArray(result.workMode) ||
        !Array.isArray(result.employmentTypes)) return null;
      if (generation.current !== owner) return null;
      ready.current = result;
      setProposal(result);
      onReady();
      return result;
    }).catch(() => null).finally(() => {
      if (generation.current === owner) setPending(false);
      if (request.current?.controller === controller) request.current = null;
    });
    request.current = { query: normalized, promise, controller };
    return promise;
  }, [locale, onReady, isUnresolvedAtomic]);

  const onInput = useCallback((query: string) => {
    clear();
    if (!canRouteQuery(query, true)) return;
    timer.current = setTimeout(() => { void requestNow(query); }, IDLE_MS);
  }, [clear, requestNow]);

  const replaceProposal = useCallback((next: QueryIntentProposal) => {
    if (ready.current?.query !== next.query || ready.current.locale !== next.locale) return;
    ready.current = next;
    setProposal(next);
  }, []);

  useEffect(() => clear, [clear]);
  useEffect(() => { clear(); }, [contextKey, locale, clear]);
  return { proposal, pending, onInput, requestNow, replaceProposal, clear };
}
