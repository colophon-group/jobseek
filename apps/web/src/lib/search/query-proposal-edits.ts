import type { QueryCandidate, QueryIntentProposal, TaxonomyCategory } from "./query-intent";

/** Apply a user's choice to one routed term without rerunning Jev. */
export function correctQueryProposal(
  proposal: QueryIntentProposal,
  termIndex: number,
  candidate: QueryCandidate | null,
): QueryIntentProposal {
  const term = proposal.terms[termIndex];
  if (!term || !["location", "occupation", "seniority", "technology"].includes(term.span.category)) return proposal;
  if (candidate && !term.alternatives.some((item) => item.id === candidate.id)) return proposal;
  const field = {
    location: "locations", occupation: "occupations",
    seniority: "seniorities", technology: "technologies",
  }[term.span.category as TaxonomyCategory] as "locations" | "occupations" | "seniorities" | "technologies";
  const values = proposal[field].filter((item) => item.id !== term.candidate?.id);
  if (candidate && !values.some((item) => item.id === candidate.id)) values.push(candidate);
  const termWords = term.span.text.split(/\s+/).map((word) => word.toLowerCase());
  const keywords = candidate
    ? proposal.keywords.filter((word) => !termWords.includes(word.toLowerCase()))
    : [...proposal.keywords, ...term.span.text.split(/\s+/).filter((word) =>
      !proposal.keywords.some((existing) => existing.toLowerCase() === word.toLowerCase()))];
  const terms = proposal.terms.map((item, index) => index === termIndex
    ? { ...item, candidate, status: candidate ? "exact" as const : "unresolved" as const }
    : item);
  return { ...proposal, [field]: values, keywords, terms };
}
