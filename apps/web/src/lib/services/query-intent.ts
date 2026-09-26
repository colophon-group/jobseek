import "server-only";

import { suggestLocations } from "./locations";
import {
  suggestOccupations,
  suggestSeniorities,
  suggestTechnologies,
} from "./taxonomy";
import {
  buildQueryIntentRequest,
  chooseQueryCandidate,
  normalizeEmploymentType,
  QUERY_INTENT_MODEL,
  QUERY_INTENT_VERSION,
  selectedRouteSpans,
  spansForQuery,
  type JevChoiceAnswer,
  type QueryCandidate,
  type QueryIntentProposal,
  type QueryTerm,
  type RoutedSpan,
  type TaxonomyCategory,
  validateJevAnswers,
  validateJevIntent,
} from "@/lib/search/query-intent";
import type { EmploymentType, WorkMode } from "@/lib/search/types";

const JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone";
const JEV_TIMEOUT_MS = 2_200;
const MAX_RESPONSE_BYTES = 128 * 1024;
const MAX_NORMALIZATION_LOOKUPS = 8;

export class QueryIntentError extends Error {
  constructor(readonly code: "disabled" | "invalid" | "unavailable", options?: ErrorOptions) {
    super(`Search query routing: ${code}`, options);
  }
}

async function suggestionsFor(span: RoutedSpan, locale: string, userLat?: number, userLng?: number): Promise<QueryCandidate[]> {
  const category = span.category as TaxonomyCategory;
  switch (category) {
    case "location": return suggestLocations({ query: span.text, locale, userLat, userLng, failOnUnavailable: true });
    case "occupation": return suggestOccupations({ query: span.text, locale, failOnUnavailable: true });
    case "seniority": return suggestSeniorities({ query: span.text, locale, failOnUnavailable: true });
    case "technology": return suggestTechnologies({ query: span.text, locale, failOnUnavailable: true });
  }
}

export async function proposeQueryFilters(params: {
  query: string;
  locale: string;
  userLat?: number;
  userLng?: number;
  signal?: AbortSignal;
}): Promise<QueryIntentProposal> {
  const token = process.env.TYPESAFE_AI_TOKEN?.trim();
  if (!token) throw new QueryIntentError("disabled");
  const query = params.query.trim();
  let request: ReturnType<typeof buildQueryIntentRequest>;
  try {
    request = buildQueryIntentRequest(query, params.locale);
  } catch {
    throw new QueryIntentError("invalid");
  }

  let response: Response;
  let body: unknown;
  try {
    response = await fetch(JEV_ENDPOINT, {
      method: "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify(request),
      signal: AbortSignal.any([AbortSignal.timeout(JEV_TIMEOUT_MS), ...(params.signal ? [params.signal] : [])]),
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`Jev HTTP ${response.status}`);
    const raw = await response.text();
    if (raw.length > MAX_RESPONSE_BYTES) throw new Error("Oversize Jev response");
    body = JSON.parse(raw);
  } catch (error) {
    throw new QueryIntentError("unavailable", { cause: error });
  }
  if (!body || typeof body !== "object" || (body as Record<string, unknown>).model !== QUERY_INTENT_MODEL) {
    throw new QueryIntentError("unavailable");
  }
  const { segments, spans } = spansForQuery(query);
  let answers: Record<string, JevChoiceAnswer>;
  let intent: "jobSearch" | "other";
  try {
    const rawAnswers = (body as Record<string, unknown>).answers as Record<string, unknown>;
    answers = validateJevAnswers(rawAnswers, spans);
    intent = validateJevIntent(rawAnswers.intent);
  } catch (error) {
    throw new QueryIntentError("unavailable", { cause: error });
  }

  const routed = selectedRouteSpans(query, answers, intent);
  // Only surviving taxonomy spans incur Typesense lookups. Deduplicate
  // repeated terms and bound work for an unusually dense 14-word query.
  const lookups = new Map<string, Promise<QueryCandidate[]>>();
  const fetched = await Promise.all(routed.map((span) => {
    if (!["location", "occupation", "seniority", "technology"].includes(span.category)) return [];
    const key = `${span.category}:${span.text.toLowerCase()}`;
    const existing = lookups.get(key);
    if (existing) return existing;
    if (lookups.size >= MAX_NORMALIZATION_LOOKUPS) return [];
    const lookup = suggestionsFor(span, params.locale, params.userLat, params.userLng).catch(() => []);
    lookups.set(key, lookup);
    return lookup;
  }));

  const consumed = segments.map((words) => words.map(() => false));
  const terms: QueryTerm[] = [];
  const locations: QueryCandidate[] = [];
  const occupations: QueryCandidate[] = [];
  const seniorities: QueryCandidate[] = [];
  const technologies: QueryCandidate[] = [];
  const workMode: WorkMode[] = [];
  const employmentTypes: EmploymentType[] = [];
  for (const [index, span] of routed.entries()) {
    let candidate: QueryCandidate | null = null;
    let status: QueryTerm["status"] = "unresolved";
    const alternatives = fetched[index].slice(0, 3);
    if (["location", "occupation", "seniority", "technology"].includes(span.category)) {
      ({ candidate, status } = chooseQueryCandidate(span.text, fetched[index]));
      if (candidate) {
        const chosenCandidate = candidate;
        const destination = {
          location: locations, occupation: occupations, seniority: seniorities, technology: technologies,
        }[span.category as TaxonomyCategory];
        if (!destination.some((item) => item.slug === chosenCandidate.slug)) destination.push(chosenCandidate);
      }
    } else if (span.category === "discard") {
      status = "exact";
    } else if (span.category === "employmentType") {
      const type = normalizeEmploymentType(span.text);
      if (type) {
        if (!employmentTypes.includes(type)) employmentTypes.push(type);
        status = "exact";
      }
    } else if (span.category === "remote" || span.category === "hybrid" || span.category === "onsite") {
      if (!workMode.includes(span.category)) workMode.push(span.category);
      status = "exact";
    }
    if (status === "exact" || status === "approximate") {
      for (let word = span.start; word < span.end; word++) consumed[span.segment][word] = true;
    }
    terms.push({ span, status, candidate, alternatives });
  }
  const keywords: string[] = [];
  const seen = new Set<string>();
  segments.forEach((words, segment) => words.forEach((word, index) => {
    if (consumed[segment][index]) return;
    const key = word.toLowerCase();
    if (!seen.has(key)) { keywords.push(word); seen.add(key); }
  }));
  return { version: QUERY_INTENT_VERSION, query, locale: params.locale, intent, keywords,
    locations, occupations, seniorities, technologies, workMode, employmentTypes, terms };
}
