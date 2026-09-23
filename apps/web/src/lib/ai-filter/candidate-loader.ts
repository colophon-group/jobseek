import "server-only";

import { and, eq, inArray } from "drizzle-orm";

import { db } from "@/db";
import {
  aiFilterConfiguration,
  aiFilterDecision,
  aiFilterQueryVersion,
  userPreferences,
  watchlist,
  watchlistCompany,
} from "@/db/schema";
import { getPostingDetail, type PostingDetail } from "@/lib/services/search";
import {
  compileWatchlistMatcherSources,
  readWatchlistCandidates,
} from "@/lib/services/watchlist-matcher";
import type { WatchlistFilters } from "@/lib/watchlist-matcher-contract";
import type { WatchlistPostingEntry } from "@/lib/watchlist-matcher-contract";
import {
  CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT,
  normalizeClassifierInputV1,
} from "./classifier-input";
import { aiFilterHistoricalHorizonStart } from "./horizon";
import type { AiFilterExecutionCandidate } from "./orchestrator";
import { AI_FILTER_MAX_SEARCH_CANDIDATES } from "./search-eligibility";

const DESCRIPTION_FETCH_TIMEOUT_MS = 5_000;
const DESCRIPTION_FETCH_MAX_BYTES = 512 * 1024;
const DESCRIPTION_FETCH_RETRIES = 1;
// One segment selects at most 50 jobs. Fetching more eagerly downloads and
// normalizes descriptions that belong to the next segment.
const CANDIDATE_OVERFETCH_LIMIT = 50;
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

function indexedMetadataHtml(candidate: WatchlistPostingEntry): string {
  const metadata = candidate.classifierMetadata;
  if (!metadata) return "";
  const rows: string[] = [];
  const add = (label: string, value: string | null | undefined) => {
    if (value?.trim()) rows.push(`<li>${escapeHtml(label)}: ${escapeHtml(value)}</li>`);
  };
  add("Locations", metadata.locations.map((location) => location.name).join(", "));
  add("Work modes", [...new Set(metadata.locations.map((location) => location.type))].join(", "));
  add("Employment type", metadata.employmentType);
  add("Seniority", metadata.seniorityName);
  add(
    "Experience",
    metadata.experienceMin == null && metadata.experienceMax == null
      ? null
      : `${metadata.experienceMin ?? "unspecified"}-${metadata.experienceMax ?? "unspecified"} years`,
  );
  add("Technologies", metadata.technologies.join(", "));
  return rows.length === 0
    ? ""
    : `<section><h2>Structured job facts</h2><ul>${rows.join("")}</ul></section>`;
}

function boundedClassifierHtml(value: string): string {
  if (value.length <= CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT) return value;
  let bounded = value.slice(0, CLASSIFIER_DESCRIPTION_HTML_CODE_UNIT_LIMIT);
  const last = bounded.charCodeAt(bounded.length - 1);
  if (last >= 0xd800 && last <= 0xdbff) bounded = bounded.slice(0, -1);
  return bounded;
}

function indexedPostingDetail(candidate: WatchlistPostingEntry): PostingDetail | null {
  const metadata = candidate.classifierMetadata;
  const descriptionLocale = metadata?.descriptionLocale;
  const r2Domain = process.env.R2_DOMAIN_URL?.replace(/\/$/, "");
  if (!metadata || !candidate.title || !candidate.company.name || !descriptionLocale || !r2Domain) {
    return null;
  }
  return {
    id: candidate.id,
    title: candidate.title,
    company: {
      ...candidate.company,
      logo: null,
    },
    locations: metadata.locations.map((location, index) => ({
      id: index,
      name: location.name,
      type: location.type,
    })),
    employmentType: metadata.employmentType,
    experienceMin: metadata.experienceMin,
    experienceMax: metadata.experienceMax,
    technologies: metadata.technologies.map((name, index) => ({ id: index, name })),
    salaryMin: metadata.salaryMin,
    salaryMax: metadata.salaryMax,
    salaryCurrency: metadata.salaryCurrency,
    salaryPeriod: metadata.salaryPeriod,
    seniority: metadata.seniorityName
      ? { id: 0, slug: "", name: metadata.seniorityName }
      : null,
    sourceUrl: candidate.sourceUrl,
    firstSeenAt: candidate.firstSeenAt,
    descriptionHtml: null,
    descriptionUrl: `${r2Domain}/job/${candidate.id}/${descriptionLocale}/latest.html`,
  };
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

async function ownedMatcher(
  ownerId: string,
  watchlistId: string,
  options: {
    candidateLanguages?: readonly string[];
    useConfiguredLanguages?: boolean;
  } = {},
) {
  const [row] = await db
    .select({
      id: watchlist.id,
      title: watchlist.title,
      filters: watchlist.filters,
      locale: userPreferences.locale,
      jobLanguages: userPreferences.jobLanguages,
      candidateLanguages: aiFilterQueryVersion.candidateLanguages,
    })
    .from(watchlist)
    .leftJoin(userPreferences, eq(userPreferences.userId, watchlist.userId))
    .leftJoin(
      aiFilterConfiguration,
      and(
        eq(aiFilterConfiguration.watchlistId, watchlist.id),
        eq(aiFilterConfiguration.ownerId, watchlist.userId),
      ),
    )
    .leftJoin(
      aiFilterQueryVersion,
      and(
        eq(aiFilterQueryVersion.configurationId, aiFilterConfiguration.id),
        eq(aiFilterQueryVersion.revision, aiFilterConfiguration.currentRevision),
      ),
    )
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
  if (options.candidateLanguages !== undefined) {
    compiled.candidateFilters.languages = [...options.candidateLanguages];
  } else if (options.useConfiguredLanguages !== false && row.candidateLanguages) {
    compiled.candidateFilters.languages = [...row.candidateLanguages];
  }
  return { compiled, locale: row.locale ?? "en" };
}

export async function countAiFilterCandidates(input: {
  ownerId: string;
  watchlistId: string;
  now?: Date;
  candidateLanguages?: readonly string[];
  signal?: AbortSignal;
}): Promise<number> {
  const now = input.now ?? new Date();
  const windowEnd = new Date(Math.floor(now.getTime() / 1_000) * 1_000 + 1_000);
  const windowStart = aiFilterHistoricalHorizonStart();
  const { compiled } = await ownedMatcher(input.ownerId, input.watchlistId, {
    candidateLanguages: input.candidateLanguages,
    useConfiguredLanguages: false,
  });
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

export async function assertAiFilterCandidateScope(input: {
  ownerId: string;
  watchlistId: string;
  candidateLanguages?: readonly string[];
  signal?: AbortSignal;
}): Promise<number> {
  const count = await countAiFilterCandidates(input);
  if (count < 1 || count > AI_FILTER_MAX_SEARCH_CANDIDATES) {
    throw new TypeError("AI filter candidate scope is not eligible");
  }
  return count;
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
  /** Repair mode: scan past candidates that already have a durable decision. */
  excludeDecidedForQueryVersionId?: string;
}): Promise<AiFilterCandidatePage> {
  const { compiled, locale } = await ownedMatcher(input.ownerId, input.watchlistId);
  const fetchImpl = input.dependencies?.fetch ?? fetch;
  const getDetail = input.dependencies?.getPostingDetail ?? getPostingDetail;
  const normalizeCandidate = async (
    candidate: WatchlistPostingEntry,
  ): Promise<AiFilterExecutionCandidate | null> => {
      try {
        const detail = indexedPostingDetail(candidate) ??
          await getDetail({ postingId: candidate.id, locale });
        const title = detail?.title ?? candidate.title;
        const companyName = detail?.company.name ?? candidate.company.name;
        if (!title || !companyName) return null;
        let description = "";
        if (detail?.descriptionUrl) {
          try {
            description = await fetchDescription(
              detail.descriptionUrl,
              fetchImpl,
              input.signal,
            );
          } catch (error) {
            // A posting whose source description has disappeared is still a
            // posting in the user's feed. Evaluate its title and indexed
            // structured facts rather than silently omitting it forever.
            if (
              !(error instanceof AiFilterCandidateLoadError) ||
              error.code !== "description_missing"
            ) {
              throw error;
            }
          }
        }
        const combinedHtml = boundedClassifierHtml(
          `${detail ? metadataHtml(detail) : indexedMetadataHtml(candidate)}${description}`,
        );
        const classifierInput = normalizeClassifierInputV1({
          candidateId: candidate.id,
          title,
          companyName,
          descriptionHtml: combinedHtml,
          selectedDescriptionLocale: detail?.descriptionUrl
            ? selectedLocale(detail.descriptionUrl)
            : candidate.classifierMetadata?.descriptionLocale ?? "und",
        });
        const postingFirstSeenAt = new Date(candidate.firstSeenAt);
        const expiresAt = new Date(input.windowEnd.getTime() + 30 * DAY_MS);
        if (!Number.isFinite(postingFirstSeenAt.getTime())) {
          return null;
        }
        return Object.freeze({
          candidateId: candidate.id,
          postingFirstSeenAt: postingFirstSeenAt.toISOString(),
          expiresAt: expiresAt.toISOString(),
          classifierInput,
        });
      } catch (error) {
        if (error instanceof AiFilterCandidateLoadError) throw error;
        throw new AiFilterCandidateLoadError("description_unavailable", {
          cause: error,
        });
      }
  };

  const selected: AiFilterExecutionCandidate[] = [];
  let scannedCount = 0;
  let total = 0;
  while (selected.length < 50) {
    let searchResult: Awaited<ReturnType<typeof readWatchlistCandidates>>;
    try {
      searchResult = await readWatchlistCandidates({
        filters: compiled.candidateFilters,
        offset: input.offset + scannedCount,
        limit: CANDIDATE_OVERFETCH_LIMIT,
        window: { windowStart: input.windowStart, windowEnd: input.windowEnd },
        order: "newest",
        requireStableOrder: true,
        includeClassifierMetadata: true,
        abortSignal: input.signal,
      });
    } catch (error) {
      throw new AiFilterCandidateLoadError("search_unavailable", { cause: error });
    }
    total = searchResult.total;
    if (searchResult.postings.length === 0) break;

    let decided = new Set<string>();
    if (input.excludeDecidedForQueryVersionId) {
      const rows = await db
        .select({ candidateId: aiFilterDecision.candidateId })
        .from(aiFilterDecision)
        .where(and(
          eq(aiFilterDecision.queryVersionId, input.excludeDecidedForQueryVersionId),
          inArray(
            aiFilterDecision.candidateId,
            searchResult.postings.map((candidate) => candidate.id),
          ),
        ));
      decided = new Set(rows.map((row) => row.candidateId));
    }
    const undecided = searchResult.postings.filter(
      (candidate) => !decided.has(candidate.id),
    );
    const outcomes = await mapWithConcurrency(undecided, 5, normalizeCandidate);
    const outcomeById = new Map(
      undecided.map((candidate, index) => [candidate.id, outcomes[index] ?? null]),
    );
    for (const candidate of searchResult.postings) {
      scannedCount += 1;
      if (!decided.has(candidate.id)) {
        const outcome = outcomeById.get(candidate.id);
        if (outcome) selected.push(outcome);
      }
      if (selected.length === 50) break;
    }
    if (
      selected.length === 50 ||
      input.offset + scannedCount >= searchResult.total
    ) {
      break;
    }
  }
  return Object.freeze({
    candidates: Object.freeze(selected),
    scannedCount,
    skippedCount: scannedCount - selected.length,
    total,
  });
}
