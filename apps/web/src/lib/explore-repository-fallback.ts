import mentionSnapshot from "@/content/blog/mention-snapshot.json";

const TYPESENSE_SEARCH_ENV_KEYS = [
  "TYPESENSE_HOST",
  "TYPESENSE_PORT",
  "TYPESENSE_PROTOCOL",
  "TYPESENSE_SEARCH_KEY",
] as const;

const EXPLORE_REPOSITORY_FALLBACK_SIZE = 10;

export interface ExploreRepositoryCompany {
  name: string;
  slug: string;
}

type TypesenseSearchEnvironment = Readonly<Record<string, string | undefined>>;

/**
 * Whether the server has enough configuration to attempt a real Typesense
 * search. Keep this separate from a search response's `degraded` bit: an
 * empty result from a configured production search must never be replaced by
 * build-only discovery data.
 */
export function hasTypesenseSearchConfiguration(
  env: TypesenseSearchEnvironment = process.env,
): boolean {
  return TYPESENSE_SEARCH_ENV_KEYS.every((key) => Boolean(env[key]?.trim()));
}

/**
 * Stable, real company identities for secretless builds and local offline
 * previews. The mention snapshot is repository-owned, generated from the
 * company registry, and independently freshness-checked by
 * `blog-mention-snapshot-gate.test.ts`.
 *
 * This deliberately exposes no posting IDs, titles, counts, or mutation
 * identifiers. Callers render profile links only and must label the data as a
 * degraded discovery fallback rather than live job results.
 */
export function getExploreRepositoryFallbackCompanies(): ExploreRepositoryCompany[] {
  return mentionSnapshot.companies
    .slice(0, EXPLORE_REPOSITORY_FALLBACK_SIZE)
    .map(({ name, slug }) => ({ name, slug }));
}
