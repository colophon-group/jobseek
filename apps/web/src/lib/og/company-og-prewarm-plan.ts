import { companyOgCacheKeyForVersion } from "@/lib/og/company-og-key";

export type CompanyOgDocument = Record<string, unknown> & {
  slug: string;
};

export type CompanyOgRenderTask = {
  company: CompanyOgDocument;
  locale: string;
  slug: string;
  reason: "changed" | "missing" | "full-rebuild";
};

export type CompanyOgPrewarmPlan = {
  tasks: CompanyOgRenderTask[];
  changedSlugs: string[];
  removedSlugs: string[];
  missingKeys: number;
};

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
        .map(([key, nested]) => [key, canonicalValue(nested)]),
    );
  }
  return value;
}

export function canonicalCompanyDocument(document: CompanyOgDocument): string {
  // Keep this projection aligned with renderCompanyOgCard. Fields carried by
  // the Typesense-compatible document but not painted on the card must not
  // cause an R2 overwrite.
  const renderModel = {
    slug: document.slug,
    name: document.name ?? null,
    icon: document.icon ?? null,
    website: document.website ?? null,
    description: document.description ?? null,
    description_de: document.description_de ?? null,
    description_fr: document.description_fr ?? null,
    description_it: document.description_it ?? null,
    industry_name: document.industry_name ?? null,
    industry_name_de: document.industry_name_de ?? null,
    industry_name_fr: document.industry_name_fr ?? null,
    industry_name_it: document.industry_name_it ?? null,
  };
  return JSON.stringify(canonicalValue(renderModel));
}

function bySlug(
  documents: CompanyOgDocument[],
  label: string,
): Map<string, CompanyOgDocument> {
  const result = new Map<string, CompanyOgDocument>();
  for (const document of documents) {
    if (!document.slug || result.has(document.slug)) {
      throw new Error(`${label} contains an invalid or duplicate company slug`);
    }
    result.set(document.slug, document);
  }
  return result;
}

/**
 * Diff the data that actually feeds the renderer, then add missing-object
 * reconciliation. CSV row order and unused columns never enter this plan.
 */
export function planCompanyOgPrewarm(input: {
  targetDocuments: CompanyOgDocument[];
  baseDocuments: CompanyOgDocument[];
  existingKeys: ReadonlySet<string>;
  rendererVersion: string;
  locales: string[];
  fullRebuild: boolean;
  maxCompanies?: number | null;
}): CompanyOgPrewarmPlan {
  const target = bySlug(input.targetDocuments, "target documents");
  const base = bySlug(input.baseDocuments, "base documents");
  const changedSlugs = [...target.entries()]
    .filter(([slug, document]) =>
      !base.has(slug) ||
      canonicalCompanyDocument(document) !==
        canonicalCompanyDocument(base.get(slug)!)
    )
    .map(([slug]) => slug)
    .sort();
  const removedSlugs = [...base.keys()]
    .filter((slug) => !target.has(slug))
    .sort();
  const changed = new Set(changedSlugs);
  const tasks: CompanyOgRenderTask[] = [];
  let missingKeys = 0;

  for (const slug of [...target.keys()].sort()) {
    const company = target.get(slug)!;
    for (const locale of input.locales) {
      const key = companyOgCacheKeyForVersion(
        input.rendererVersion,
        locale,
        slug,
      );
      const missing = !input.existingKeys.has(key);
      if (missing) missingKeys += 1;
      if (!input.fullRebuild && !changed.has(slug) && !missing) continue;
      tasks.push({
        company,
        locale,
        slug,
        reason: input.fullRebuild
          ? "full-rebuild"
          : changed.has(slug) ? "changed" : "missing",
      });
    }
  }

  if (input.maxCompanies !== null && input.maxCompanies !== undefined) {
    const allowed = new Set(
      [...new Set(tasks.map((task) => task.slug))]
        .slice(0, input.maxCompanies),
    );
    return {
      tasks: tasks.filter((task) => allowed.has(task.slug)),
      changedSlugs,
      removedSlugs,
      missingKeys,
    };
  }

  return { tasks, changedSlugs, removedSlugs, missingKeys };
}
