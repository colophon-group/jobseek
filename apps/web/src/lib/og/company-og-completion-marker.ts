export const COMPANY_OG_COMPLETION_SCHEMA_VERSION = 2 as const;

const REVISION = /^[a-f0-9]{40}$/;
const VERSION = /^[a-z0-9-]{1,120}$/;

export type CompanyOgCompletionMarker = {
  schemaVersion: typeof COMPANY_OG_COMPLETION_SCHEMA_VERSION;
  complete: true;
  rendererVersion: string;
  sourceVersion: string;
  revision: string;
  baseRevision: string | null;
  companies: number;
  locales: string[];
  expected: number;
  completedAt: string;
};

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

/** Parse only the revision-aware marker schema. Legacy pointers fail closed. */
export function parseCompanyOgCompletionMarker(
  value: unknown,
  expectedRendererVersion?: string,
): CompanyOgCompletionMarker | null {
  if (!value || typeof value !== "object") return null;
  const marker = value as Record<string, unknown>;
  if (
    marker.schemaVersion !== COMPANY_OG_COMPLETION_SCHEMA_VERSION ||
    marker.complete !== true ||
    typeof marker.rendererVersion !== "string" ||
    !VERSION.test(marker.rendererVersion) ||
    (expectedRendererVersion !== undefined &&
      marker.rendererVersion !== expectedRendererVersion) ||
    typeof marker.sourceVersion !== "string" ||
    !VERSION.test(marker.sourceVersion) ||
    typeof marker.revision !== "string" ||
    !REVISION.test(marker.revision) ||
    (marker.baseRevision !== null &&
      (typeof marker.baseRevision !== "string" ||
        !REVISION.test(marker.baseRevision))) ||
    !isNonNegativeInteger(marker.companies) ||
    !Array.isArray(marker.locales) ||
    marker.locales.length === 0 ||
    marker.locales.some((locale) =>
      typeof locale !== "string" || !VERSION.test(locale)
    ) ||
    new Set(marker.locales).size !== marker.locales.length ||
    !isNonNegativeInteger(marker.expected) ||
    marker.expected !== marker.companies * marker.locales.length ||
    typeof marker.completedAt !== "string" ||
    !Number.isFinite(Date.parse(marker.completedAt))
  ) {
    return null;
  }

  return marker as CompanyOgCompletionMarker;
}

export function sameCompanyOgCompletionMarker(
  left: CompanyOgCompletionMarker,
  right: CompanyOgCompletionMarker,
): boolean {
  return left.schemaVersion === right.schemaVersion &&
    left.complete === right.complete &&
    left.rendererVersion === right.rendererVersion &&
    left.sourceVersion === right.sourceVersion &&
    left.revision === right.revision &&
    left.baseRevision === right.baseRevision &&
    left.companies === right.companies &&
    left.expected === right.expected &&
    left.completedAt === right.completedAt &&
    left.locales.length === right.locales.length &&
    left.locales.every((locale, index) => locale === right.locales[index]);
}

/** Compare only the immutable proof that a source's complete card set exists. */
export function sameCompanyOgCompletionCoverage(
  left: CompanyOgCompletionMarker,
  right: CompanyOgCompletionMarker,
): boolean {
  return left.schemaVersion === right.schemaVersion &&
    left.complete === right.complete &&
    left.rendererVersion === right.rendererVersion &&
    left.sourceVersion === right.sourceVersion &&
    left.companies === right.companies &&
    left.expected === right.expected &&
    left.locales.length === right.locales.length &&
    left.locales.every((locale) => right.locales.includes(locale));
}
