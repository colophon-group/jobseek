import policy from "./jev-query-policy.json";
import type { EmploymentType, WorkMode } from "./types";

export const QUERY_INTENT_VERSION = policy.version;
export const QUERY_INTENT_MODEL = policy.model;
export const QUERY_INTENT_MAX_CHARS = 180;
export const QUERY_INTENT_MAX_SPANS = 40;
export const QUERY_INTENT_THRESHOLD = 0.6;
const OCCUPATION_CATALOG_BY_LOCALE: Record<string, string> = Object.fromEntries(
  ["en", "de", "fr", "it"].map((locale) => [locale,
    policy.occupationCatalog.map((row) => row[locale as keyof typeof row] || row.en).join(" | ")]),
);

export type QueryIntentCategory =
  | "keyword" | "discard" | "location" | "occupation" | "seniority" | "technology"
  | "remote" | "hybrid" | "onsite" | "employmentType";
export type RoutedCategory = Exclude<QueryIntentCategory, "keyword">;
export type TaxonomyCategory = Extract<RoutedCategory, "location" | "occupation" | "seniority" | "technology">;

export type QuerySpan = Readonly<{
  id: string;
  segment: number;
  start: number;
  end: number;
  text: string;
}>;

export type JevChoiceAnswer = Readonly<{
  type: "choice";
  choice: QueryIntentCategory;
  probabilities: Readonly<Record<QueryIntentCategory, number>>;
}>;

export type RoutedSpan = QuerySpan & Readonly<{
  category: RoutedCategory;
  probability: number;
}>;

export type QueryCandidate = Readonly<{
  id: number;
  slug: string;
  name: string;
  matchedName?: string;
  type?: string;
  parentName?: string | null;
}>;

export type QueryTerm = Readonly<{
  span: RoutedSpan;
  status: "exact" | "approximate" | "ambiguous" | "unresolved";
  candidate: QueryCandidate | null;
  alternatives: readonly QueryCandidate[];
}>;

export type QueryIntentProposal = Readonly<{
  version: typeof QUERY_INTENT_VERSION;
  query: string;
  locale: string;
  intent: "jobSearch" | "other";
  keywords: readonly string[];
  locations: readonly QueryCandidate[];
  occupations: readonly QueryCandidate[];
  seniorities: readonly QueryCandidate[];
  technologies: readonly QueryCandidate[];
  workMode: readonly WorkMode[];
  employmentTypes: readonly EmploymentType[];
  terms: readonly QueryTerm[];
}>;

export function tokenizeQuery(query: string): string[][] {
  return query.split(/[,\n\r\t/|]+/).map((part) => part.trim()).filter(Boolean)
    .map((part) => part.split(/\s+/).filter(Boolean));
}

export function spansForQuery(query: string): { segments: string[][]; spans: QuerySpan[] } {
  const segments = tokenizeQuery(query);
  const spans: QuerySpan[] = [];
  for (let segment = 0; segment < segments.length; segment++) {
    const words = segments[segment];
    for (let start = 0; start < words.length; start++) {
      for (let length = 1; length <= 3 && start + length <= words.length; length++) {
        spans.push({ id: `s_${spans.length}`, segment, start, end: start + length,
          text: words.slice(start, start + length).join(" ") });
      }
    }
  }
  return { segments, spans };
}

export function validateQueryIntentQuery(query: string): QuerySpan[] {
  const { spans } = spansForQuery(query);
  if (query.trim().length < 2 || query.length > QUERY_INTENT_MAX_CHARS || spans.length > QUERY_INTENT_MAX_SPANS) {
    throw new RangeError("Search query exceeds Jev routing bounds");
  }
  return spans;
}

export function buildQueryIntentRequest(query: string, locale: string) {
  const spans = validateQueryIntentQuery(query);
  const questions: Record<string, { type: "choice"; instructions: string; criteria: Record<string, string> }> = Object.fromEntries(spans.map((span) => [span.id, {
    type: "choice",
    instructions: `For span ${span.id} (${span.text}), choose its role in this exact query, following state.policy.`,
    criteria: policy.categories,
  }]));
  questions.intent = {
    type: "choice",
    instructions: "Classify the whole query before interpreting spans. Does it ask to find job postings, or is it an informational/unrelated request?",
    criteria: policy.intentCriteria,
  };
  return {
    model: QUERY_INTENT_MODEL,
    state: {
      query,
      locale,
      policy: policy.policy,
      occupationCatalog: OCCUPATION_CATALOG_BY_LOCALE[locale] ?? OCCUPATION_CATALOG_BY_LOCALE.en,
      spans,
    },
    questions,
  };
}

export function validateJevIntent(value: unknown): "jobSearch" | "other" {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError("Invalid Jev intent");
  const answer = value as Record<string, unknown>;
  if (answer.type !== "choice" || !["jobSearch", "other"].includes(answer.choice as string)) {
    throw new TypeError("Invalid Jev intent");
  }
  const probabilities = answer.probabilities as Record<string, unknown> | undefined;
  if (!probabilities || ["jobSearch", "other"].some((key) => typeof probabilities[key] !== "number" ||
    !Number.isFinite(probabilities[key]) || (probabilities[key] as number) < 0 ||
    (probabilities[key] as number) > 1)) throw new TypeError("Invalid Jev intent probabilities");
  return answer.choice as "jobSearch" | "other";
}

export function validateJevAnswers(value: unknown, spans: readonly QuerySpan[]): Record<string, JevChoiceAnswer> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError("Invalid Jev answers");
  const record = value as Record<string, unknown>;
  if (Object.keys(record).length !== spans.length + ("intent" in record ? 1 : 0)) throw new TypeError("Jev answer count mismatch");
  const categories = Object.keys(policy.categories) as QueryIntentCategory[];
  const answers: Record<string, JevChoiceAnswer> = {};
  for (const span of spans) {
    const answer = record[span.id];
    if (!answer || typeof answer !== "object" || Array.isArray(answer)) throw new TypeError("Invalid Jev answer");
    const item = answer as Record<string, unknown>;
    if (item.type !== "choice" || !categories.includes(item.choice as QueryIntentCategory)) {
      throw new TypeError("Invalid Jev choice");
    }
    const probabilities = item.probabilities;
    if (!probabilities || typeof probabilities !== "object" || Array.isArray(probabilities)) {
      throw new TypeError("Invalid Jev probabilities");
    }
    const p = probabilities as Record<string, unknown>;
    if (categories.some((category) => typeof p[category] !== "number" ||
      !Number.isFinite(p[category]) || (p[category] as number) < 0 || (p[category] as number) > 1)) {
      throw new TypeError("Invalid Jev probability");
    }
    answers[span.id] = item as JevChoiceAnswer;
  }
  return answers;
}

export function selectedRouteSpans(query: string, answers: Readonly<Record<string, JevChoiceAnswer>>, intent: "jobSearch" | "other" = "jobSearch"): RoutedSpan[] {
  if (intent === "other") return [];
  const { segments, spans } = spansForQuery(query);
  const consumed = segments.map((words) => words.map(() => false));
  const ordered = [...spans].sort((a, b) => {
    const aChoice = answers[a.id]?.choice;
    const bChoice = answers[b.id]?.choice;
    // Discard can consume only words left after useful filter spans win.
    if ((aChoice === "discard") !== (bChoice === "discard")) return aChoice === "discard" ? 1 : -1;
    const aConfidence = aChoice ? answers[a.id]?.probabilities[aChoice] ?? 0 : 0;
    const bConfidence = bChoice ? answers[b.id]?.probabilities[bChoice] ?? 0 : 0;
    return bConfidence - aConfidence || a.segment - b.segment || a.start - b.start;
  });
  const result: RoutedSpan[] = [];
  for (const span of ordered) {
    const answer = answers[span.id];
    const category = answer?.choice;
    const probability = category ? answer.probabilities[category] : 0;
    if (!category || category === "keyword" || probability < QUERY_INTENT_THRESHOLD ||
      consumed[span.segment].slice(span.start, span.end).some(Boolean)) continue;
    for (let index = span.start; index < span.end; index++) consumed[span.segment][index] = true;
    result.push({ ...span, category, probability });
  }
  return result.sort((a, b) => a.segment - b.segment || a.start - b.start);
}

export function normalizeEmploymentType(text: string): EmploymentType | null {
  const value = text.toLowerCase().trim();
  if (["contract", "contractor"].includes(value)) return "contract";
  if (["part-time", "part time", "teilzeit", "temps partiel"].includes(value)) return "part_time";
  if (["full-time", "full time", "vollzeit"].includes(value)) return "full_time";
  if (["temporary", "temp"].includes(value)) return "temporary";
  if (["volunteer", "voluntary"].includes(value)) return "volunteer";
  return null;
}

function normalizeText(value: string): string {
  return value.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}]+/gu, "");
}

function editDistance(left: string, right: string): number {
  if (left === right) return 0;
  const previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  for (let i = 1; i <= left.length; i++) {
    const current = [i];
    for (let j = 1; j <= right.length; j++) {
      current[j] = Math.min(
        current[j - 1] + 1,
        previous[j] + 1,
        previous[j - 1] + (left[i - 1] === right[j - 1] ? 0 : 1),
      );
    }
    for (let j = 0; j < current.length; j++) previous[j] = current[j];
  }
  return previous[right.length];
}

function candidateDistance(text: string, candidate: QueryCandidate): number {
  const query = normalizeText(text);
  return Math.min(
    editDistance(query, normalizeText(candidate.name)),
    candidate.matchedName ? editDistance(query, normalizeText(candidate.matchedName)) : Infinity,
  );
}

/** Resolve exact aliases first. A fuzzy hit must be close and clearly ahead. */
export function chooseQueryCandidate(text: string, candidates: readonly QueryCandidate[]): {
  candidate: QueryCandidate | null;
  status: QueryTerm["status"];
} {
  const query = normalizeText(text);
  const exact = candidates.filter((candidate) =>
    [candidate.name, candidate.slug, candidate.matchedName]
      .some((name) => name && normalizeText(name) === query));
  if (exact.length === 1) return { candidate: exact[0], status: "exact" };
  if (exact.length > 1) return { candidate: null, status: "ambiguous" };
  if (candidates.length === 0) return { candidate: null, status: "unresolved" };

  const ranked = candidates.map((candidate) => ({ candidate, distance: candidateDistance(text, candidate) }))
    .sort((a, b) => a.distance - b.distance);
  const best = ranked[0];
  const maximumDistance = Math.max(1, Math.floor(query.length * 0.22));
  if (query.length < 4 || best.distance > maximumDistance) {
    return { candidate: null, status: "unresolved" };
  }
  if (ranked[1] && ranked[1].distance <= best.distance) {
    return { candidate: null, status: "ambiguous" };
  }
  return { candidate: best.candidate, status: "approximate" };
}
