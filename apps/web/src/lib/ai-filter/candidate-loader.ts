import "server-only";

import { and, eq } from "drizzle-orm";

import { db } from "@/db";
import { userPreferences, watchlist, watchlistCompany } from "@/db/schema";
import { getPostingDetail, type PostingDetail } from "@/lib/services/search";
import {
  compileWatchlistMatcherSources,
  readWatchlistCandidates,
} from "@/lib/services/watchlist-matcher";
import type { WatchlistFilters } from "@/lib/watchlist-matcher-contract";
import {
  CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT,
  normalizeClassifierInputV1,
} from "./classifier-input";
import type { AiFilterExecutionCandidate } from "./orchestrator";

const DESCRIPTION_FETCH_TIMEOUT_MS = 5_000;
const DESCRIPTION_FETCH_MAX_BYTES = 512 * 1024;
const DESCRIPTION_FETCH_RETRIES = 1;
const CANDIDATE_OVERFETCH_LIMIT = 100;
const RETRYABLE_DESCRIPTION_STATUS = new Set([429, 500, 502, 503, 504]);
const DAY_MS = 24 * 60 * 60 * 1_000;

export class AiFilterCandidateLoadError extends Error {
  readonly code:
    | "not_found"
    | "search_unavailable"
    | "description_missing"
    | "description_unavailable";

  constructor(code: AiFilterCandidateLoadError["code"], options?: { cause?: unknown }) {
    super(`AI filter candidate load failed: ${code}`, { cause: options?.cause });
    this.name = "AiFilterCandidateLoadError";
    this.code = code;
  }
}

export type AiFilterCandidatePage = Readonly<{
  candidates: readonly AiFilterExecutionCandidate[];
  scannedCount: number;
  skippedCount: number;
  total: number;
}>;

type CandidateLoaderDependencies = Readonly<{
  fetch?: typeof fetch;
  getPostingDetail?: typeof getPostingDetail;
}>;

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function metadataHtml(posting: PostingDetail): string {
  const rows: string[] = [];
  const add = (label: string, value: string | null | undefined) => {
    if (value?.trim()) rows.push(`<li>${escapeHtml(label)}: ${escapeHtml(value)}</li>`);
  };
  add("Locations", posting.locations.map((location) => location.name).join(", "));
  add("Work modes", [...new Set(posting.locations.map((location) => location.type))].join(", "));
  add("Employment type", posting.employmentType);
  add("Seniority", posting.seniority?.name);
  add(
    "Experience",
    posting.experienceMin == null && posting.experienceMax == null
      ? null
      : `${posting.experienceMin ?? "unspecified"}-${posting.experienceMax ?? "unspecified"} years`,
  );
  add(
    "Salary",
    posting.salaryMin == null && posting.salaryMax == null
      ? null
      : `${posting.salaryMin ?? "unspecified"}-${posting.salaryMax ?? "unspecified"} ${posting.salaryCurrency ?? ""} ${posting.salaryPeriod ?? ""}`.trim(),
  );
  add("Technologies", posting.technologies.map((technology) => technology.name).join(", "));
  return rows.length === 0
    ? ""
    : `<section><h2>Structured job facts</h2><ul>${rows.join("")}</ul></section>`;
}

function selectedLocale(descriptionUrl: string): string {
  try {
    const segments = new URL(descriptionUrl).pathname.split("/").filter(Boolean);
    const locale = segments.at(-2);
    return locale && /^[a-z]{2}(?:-[A-Z]{2})?$/.test(locale) ? locale : "und";
  } catch {
    return "und";
  }
}

async function readBoundedText(response: Response): Promise<string> {
  const contentLength = response.headers.get("content-length");
  if (contentLength && Number(contentLength) > DESCRIPTION_FETCH_MAX_BYTES) {
    throw new AiFilterCandidateLoadError("description_unavailable");
  }
  if (!response.body) throw new AiFilterCandidateLoadError("description_unavailable");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > DESCRIPTION_FETCH_MAX_BYTES) {
      await reader.cancel();
      throw new AiFilterCandidateLoadError("description_unavailable");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    throw new AiFilterCandidateLoadError("description_unavailable", { cause: error });
  }
}

async function fetchDescription(
  descriptionUrl: string,
  fetchImpl: typeof fetch,
  signal?: AbortSignal,
): Promise<string> {
  for (let attempt = 0; attempt <= DESCRIPTION_FETCH_RETRIES; attempt += 1) {
    const controller = new AbortController();
    const abortFromParent = () => controller.abort(signal?.reason);
    signal?.addEventListener("abort", abortFromParent, { once: true });
    const timer = setTimeout(() => controller.abort(), DESCRIPTION_FETCH_TIMEOUT_MS);
    try {
      const response = await fetchImpl(descriptionUrl, {
        headers: { accept: "text/html,application/xhtml+xml" },
        signal: controller.signal,
      });
      if (response.ok) {
        const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
        if (contentType && !contentType.includes("text/html")) {
          throw new AiFilterCandidateLoadError("description_unavailable");
        }
        return await readBoundedText(response);
      }
      if (response.status === 404) {
        throw new AiFilterCandidateLoadError("description_missing");
      }
      if (!RETRYABLE_DESCRIPTION_STATUS.has(response.status) || attempt === DESCRIPTION_FETCH_RETRIES) {
        throw new AiFilterCandidateLoadError("description_unavailable");
      }
    } catch (error) {
      if (signal?.aborted) {
        throw new AiFilterCandidateLoadError("description_unavailable", { cause: error });
      }
      if (error instanceof AiFilterCandidateLoadError || attempt === DESCRIPTION_FETCH_RETRIES) {
        throw error instanceof AiFilterCandidateLoadError
          ? error
          : new AiFilterCandidateLoadError("description_unavailable", { cause: error });
      }
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abortFromParent);
    }
  }
  throw new AiFilterCandidateLoadError("description_unavailable");
}

async function mapWithConcurrency<T, R>(
  values: readonly T[],
  concurrency: number,
  mapper: (value: T) => Promise<R>,
): Promise<R[]> {
  const output = new Array<R>(values.length);
  let next = 0;
  await Promise.all(Array.from(
    { length: Math.min(concurrency, values.length) },
    async () => {
      while (next < values.length) {
        const index = next++;
        output[index] = await mapper(values[index]!);
      }
    },
  ));
  return output;
}

async function ownedMatcher(ownerId: string, watchlistId: string) {
  const [row] = await db
    .select({
      id: watchlist.id,
      title: watchlist.title,
      filters: watchlist.filters,
      locale: userPreferences.locale,
      jobLanguages: userPreferences.jobLanguages,
    })
    .from(watchlist)
    .leftJoin(userPreferences, eq(userPreferences.userId, watchlist.userId))
    .where(and(eq(watchlist.id, watchlistId), eq(watchlist.userId, ownerId)))
    .limit(1);
  if (!row) throw new AiFilterCandidateLoadError("not_found");
  const companies = await db
    .select({ companyId: watchlistCompany.companyId })
    .from(watchlistCompany)
    .where(eq(watchlistCompany.watchlistId, watchlistId));
  const [compiled] = await compileWatchlistMatcherSources([{
    watchlistId: row.id,
    watchlistLabel: row.title,
    filters: row.filters as WatchlistFilters,
    companyIds: companies.map((company) => company.companyId),
    locale: row.locale ?? "en",
    jobLanguages: row.jobLanguages ?? [],
  }]);
  if (!compiled) throw new AiFilterCandidateLoadError("search_unavailable");
  return { compiled, locale: row.locale ?? "en" };
}

export async function countAiFilterCandidates(input: {
  ownerId: string;
  watchlistId: string;
  now?: Date;
  signal?: AbortSignal;
}): Promise<number> {
  const now = input.now ?? new Date();
  const windowEnd = new Date(Math.floor(now.getTime() / 1_000) * 1_000 + 1_000);
  const windowStart = new Date(windowEnd.getTime() - 30 * DAY_MS);
  const { compiled } = await ownedMatcher(input.ownerId, input.watchlistId);
  try {
    const result = await readWatchlistCandidates({
      filters: compiled.candidateFilters,
      offset: 0,
      limit: 0,
      window: { windowStart, windowEnd },
      order: "newest",
      requireStableOrder: true,
      abortSignal: input.signal,
    });
    return result.total;
  } catch (error) {
    throw new AiFilterCandidateLoadError("search_unavailable", { cause: error });
  }
}

/** Stable current hard-filter membership for accepted/rejected product reads. */
export async function loadAiFilterDecisionCandidates(input: {
  ownerId: string;
  watchlistId: string;
  offset: number;
  limit: number;
  now?: Date;
  signal?: AbortSignal;
}) {
  if (!Number.isSafeInteger(input.offset) || input.offset < 0) {
    throw new TypeError("AI filter decision offset is invalid");
  }
  if (!Number.isSafeInteger(input.limit) || input.limit < 1 || input.limit > 100) {
    throw new TypeError("AI filter decision page size is invalid");
  }
  const now = input.now ?? new Date();
  const windowEnd = new Date(Math.floor(now.getTime() / 1_000) * 1_000 + 1_000);
  const windowStart = new Date(windowEnd.getTime() - 30 * DAY_MS);
  const { compiled } = await ownedMatcher(input.ownerId, input.watchlistId);
  try {
    return await readWatchlistCandidates({
      filters: compiled.candidateFilters,
      offset: input.offset,
      limit: input.limit,
      window: { windowStart, windowEnd },
      order: "newest",
      requireStableOrder: true,
      abortSignal: input.signal,
    });
  } catch (error) {
    throw new AiFilterCandidateLoadError("search_unavailable", { cause: error });
  }
}

/** Loads and normalizes up to 50 usable candidates from a bounded source page. */
export async function loadAiFilterCandidatePage(input: {
  ownerId: string;
  watchlistId: string;
  offset: number;
  windowStart: Date;
  windowEnd: Date;
  signal?: AbortSignal;
  dependencies?: CandidateLoaderDependencies;
}): Promise<AiFilterCandidatePage> {
  const { compiled, locale } = await ownedMatcher(input.ownerId, input.watchlistId);
  let searchResult: Awaited<ReturnType<typeof readWatchlistCandidates>>;
  try {
    searchResult = await readWatchlistCandidates({
      filters: compiled.candidateFilters,
      offset: input.offset,
      limit: CANDIDATE_OVERFETCH_LIMIT,
      window: { windowStart: input.windowStart, windowEnd: input.windowEnd },
      order: "newest",
      requireStableOrder: true,
      abortSignal: input.signal,
    });
  } catch (error) {
    throw new AiFilterCandidateLoadError("search_unavailable", { cause: error });
  }

  const fetchImpl = input.dependencies?.fetch ?? fetch;
  const getDetail = input.dependencies?.getPostingDetail ?? getPostingDetail;
  const outcomes = await mapWithConcurrency(
    searchResult.postings,
    5,
    async (candidate): Promise<AiFilterExecutionCandidate | null> => {
      try {
        const detail = await getDetail({ postingId: candidate.id, locale });
        if (!detail?.descriptionUrl || !detail.title || !detail.company.name) return null;
        const description = await fetchDescription(
          detail.descriptionUrl,
          fetchImpl,
          input.signal,
        );
        const combinedHtml = `${metadataHtml(detail)}${description}`;
        if (combinedHtml.length > CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT) {
          return null;
        }
        const classifierInput = normalizeClassifierInputV1({
          candidateId: candidate.id,
          title: detail.title,
          companyName: detail.company.name,
          descriptionHtml: combinedHtml,
          selectedDescriptionLocale: selectedLocale(detail.descriptionUrl),
        });
        const postingFirstSeenAt = new Date(candidate.firstSeenAt);
        const expiresAt = new Date(postingFirstSeenAt.getTime() + 30 * DAY_MS);
        if (
          !Number.isFinite(postingFirstSeenAt.getTime()) ||
          expiresAt.getTime() <= input.windowEnd.getTime()
        ) {
          return null;
        }
        return Object.freeze({
          candidateId: candidate.id,
          postingFirstSeenAt: postingFirstSeenAt.toISOString(),
          expiresAt: expiresAt.toISOString(),
          classifierInput,
        });
      } catch (error) {
        if (
          error instanceof AiFilterCandidateLoadError &&
          error.code === "description_missing"
        ) {
          return null;
        }
        if (error instanceof AiFilterCandidateLoadError) throw error;
        throw new AiFilterCandidateLoadError("description_unavailable", {
          cause: error,
        });
      }
    },
  );
  const selected: AiFilterExecutionCandidate[] = [];
  let scannedCount = 0;
  for (const outcome of outcomes) {
    scannedCount += 1;
    if (outcome) selected.push(outcome);
    if (selected.length === 50) break;
  }
  return Object.freeze({
    candidates: Object.freeze(selected),
    scannedCount,
    skippedCount: scannedCount - selected.length,
    total: searchResult.total,
  });
}
