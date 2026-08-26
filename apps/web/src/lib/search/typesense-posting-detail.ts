import "server-only";

import { normalizePostingTitle } from "@/lib/posting-title";
import { logExternalError } from "@/lib/safe-external-error";
import { getSearchClient } from "./typesense-client";
import { withTypesenseRetry } from "./typesense-retry";

interface JobPostingDocument extends Record<string, unknown> {
  id: string;
  company_id: string;
  company_name: string;
  company_slug: string;
  company_icon?: string;
  title: string;
  is_active: boolean;
  location_ids: number[];
  location_names: string[];
  location_types: string[];
  location_geo_types: string[];
  seniority_id?: number;
  seniority_name?: string;
  technology_ids: number[];
  technology_names: string[];
  employment_type?: string;
  salary_min?: number;
  salary_max?: number;
  salary_currency?: string;
  salary_period?: string;
  experience_min_years?: number;
  experience_max_years?: number;
  experience_min: number;
  experience_max?: number;
  locales: string[];
  source_url?: string;
  first_seen_at: number;
}

interface CompanyDocument extends Record<string, unknown> {
  id: string;
  name: string;
  slug: string;
  logo?: string;
  icon?: string;
}

interface LocationDocument extends Record<string, unknown> {
  id: string;
  location_id: number;
  name_en: string;
  name_de?: string;
  name_fr?: string;
  name_it?: string;
  type: string;
  parent_name?: string;
}

interface SeniorityDocument extends Record<string, unknown> {
  id: string;
  seniority_id: number;
  slug: string;
  name: string;
  locale: string;
}

export interface IndexedPostingSnapshot {
  id: string;
  title: string;
  sourceUrl: string;
  firstSeenAt: string;
  isActive: boolean;
  salaryMin: number | null;
  salaryMax: number | null;
  salaryCurrency: string | null;
  salaryPeriod: string | null;
  company: {
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  };
}

export interface IndexedPostingDetail extends IndexedPostingSnapshot {
  company: IndexedPostingSnapshot["company"] & { logo: string | null };
  locations: {
    id: number;
    name: string;
    type: string;
    geoType?: string;
    parentName?: string;
  }[];
  employmentType: string | null;
  experienceMin: number | null;
  experienceMax: number | null;
  technologies: { id: number; name: string }[];
  seniority: { id: number; slug: string; name: string } | null;
  descriptionLocale: string;
}

export interface IndexedPostingState {
  isActive: boolean;
  sourceUrl: string;
}

const SAFE_DOCUMENT_ID_RE = /^[A-Za-z0-9_-]+$/;
const MAX_POSTING_STATE_BATCH = 250;

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`Typesense posting snapshot has invalid ${field}`);
  }
  return value;
}

function optionalNumber(value: unknown, field: string): number | null {
  if (value == null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Typesense posting snapshot has invalid ${field}`);
  }
  return value;
}

function optionalString(value: unknown, field: string): string | null {
  if (value == null) return null;
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`Typesense posting snapshot has invalid ${field}`);
  }
  return value;
}

async function retrieveDocument<T extends Record<string, unknown>>(
  collection: string,
  id: string,
  label: string,
): Promise<T> {
  return withTypesenseRetry(
    () =>
      getSearchClient()
        .collections<T>(collection)
        .documents(id)
        .retrieve(),
    { label },
  );
}

async function retrieveOptionalDocument<T extends Record<string, unknown>>(
  collection: string,
  id: string,
  label: string,
): Promise<T | null> {
  try {
    return await retrieveDocument<T>(collection, id, label);
  } catch (error) {
    logExternalError("warn", { service: "typesense", operation: label }, error);
    return null;
  }
}

function mapSnapshot(doc: JobPostingDocument): IndexedPostingSnapshot {
  const id = requiredString(doc.id, "id");
  if (!SAFE_DOCUMENT_ID_RE.test(id)) {
    throw new Error("Typesense posting snapshot has an unsafe id");
  }
  const title = normalizePostingTitle(requiredString(doc.title, "title"));
  if (!title) throw new Error("Typesense posting snapshot has an empty title");
  if (typeof doc.is_active !== "boolean") {
    throw new Error("Typesense posting snapshot has invalid is_active");
  }
  if (
    typeof doc.first_seen_at !== "number" ||
    !Number.isFinite(doc.first_seen_at) ||
    doc.first_seen_at <= 0
  ) {
    throw new Error("Typesense posting snapshot has invalid first_seen_at");
  }

  return {
    id,
    title,
    sourceUrl: requiredString(doc.source_url, "source_url"),
    firstSeenAt: new Date(doc.first_seen_at * 1000).toISOString(),
    isActive: doc.is_active,
    salaryMin: optionalNumber(doc.salary_min, "salary_min"),
    salaryMax: optionalNumber(doc.salary_max, "salary_max"),
    salaryCurrency: optionalString(doc.salary_currency, "salary_currency"),
    salaryPeriod: optionalString(doc.salary_period, "salary_period"),
    company: {
      id: requiredString(doc.company_id, "company_id"),
      name: requiredString(doc.company_name, "company_name"),
      slug: requiredString(doc.company_slug, "company_slug"),
      icon: optionalString(doc.company_icon, "company_icon"),
    },
  };
}

function normalizeExperience(value: number | undefined, isMaximum: boolean): number | null {
  if (value == null || value < 0) return null;
  if (isMaximum && value === 99) return null;
  return value;
}

function supportedLocale(locale: string): "en" | "de" | "fr" | "it" {
  return locale === "de" || locale === "fr" || locale === "it" ? locale : "en";
}

/**
 * Retrieve and validate the immutable fields copied onto a saved-job row.
 * New saves fail closed when Typesense is unavailable or incomplete; otherwise
 * the database would retain an application row that cannot survive mirror
 * removal. Unsaving an existing row does not call this function.
 */
export async function fetchIndexedPostingSnapshot(
  postingId: string,
): Promise<IndexedPostingSnapshot> {
  const doc = await retrieveDocument<JobPostingDocument>(
    "job_posting",
    postingId,
    `savedJobSnapshot[${postingId}]`,
  );
  return mapSnapshot(doc);
}

/**
 * Resolve the saved-job fields that intentionally stay live. A single
 * Typesense search covers a page of posting ids; missing hits and outages are
 * omitted so callers can retain their immutable snapshot values.
 * ``sourceUrl`` follows publication/locale changes for the same posting UUID;
 * the stored snapshot remains the outage fallback.
 */
export async function fetchIndexedPostingStates(
  postingIds: string[],
): Promise<Map<string, IndexedPostingState>> {
  const ids = [...new Set(postingIds)]
    .filter((id) => SAFE_DOCUMENT_ID_RE.test(id))
    .slice(0, MAX_POSTING_STATE_BATCH);
  if (ids.length === 0) return new Map();

  try {
    const result = await withTypesenseRetry(
      () =>
        getSearchClient()
          .collections<JobPostingDocument>("job_posting")
          .documents()
          .search({
            q: "*",
            filter_by: `id:[${ids.join(",")}]`,
            include_fields: "id,is_active,source_url",
            per_page: ids.length,
          }),
      { label: `savedJobStates[${ids.length}]` },
    );

    return new Map(
      (result.hits ?? []).map((hit) => [
        hit.document.id,
        {
          isActive: hit.document.is_active,
          sourceUrl: requiredString(hit.document.source_url, "source_url"),
        },
      ]),
    );
  } catch (error) {
    logExternalError("warn", { service: "typesense", operation: "saved_job_states" }, error);
    return new Map();
  }
}

/** Retrieve a complete posting-detail projection without Supabase joins. */
export async function fetchIndexedPostingDetail(
  postingId: string,
  requestedLocale: string,
): Promise<IndexedPostingDetail | null> {
  try {
    const doc = await retrieveDocument<JobPostingDocument>(
      "job_posting",
      postingId,
      `postingDetail[${postingId}]`,
    );
    const locale = supportedLocale(requestedLocale);
    const leafLocationIds = (doc.location_ids ?? []).slice(
      0,
      doc.location_names?.length ?? 0,
    );

    const companyPromise = retrieveOptionalDocument<CompanyDocument>(
      "company",
      doc.company_id,
      `postingCompany[${doc.company_id}]`,
    );
    const locationPromises = leafLocationIds.map((locationId) =>
      retrieveOptionalDocument<LocationDocument>(
        "location",
        String(locationId),
        `postingLocation[${locationId}]`,
      ),
    );

    let seniorityPromise: Promise<SeniorityDocument | null> = Promise.resolve(null);
    if (doc.seniority_id != null) {
      seniorityPromise = retrieveOptionalDocument<SeniorityDocument>(
        "seniority",
        `${doc.seniority_id}-${locale}`,
        `postingSeniority[${doc.seniority_id}-${locale}]`,
      ).then((localized) => {
        if (localized || locale === "en") return localized;
        return retrieveOptionalDocument<SeniorityDocument>(
          "seniority",
          `${doc.seniority_id}-en`,
          `postingSeniority[${doc.seniority_id}-en]`,
        );
      });
    }

    const [company, locationDocs, seniority] = await Promise.all([
      companyPromise,
      Promise.all(locationPromises),
      seniorityPromise,
    ]);
    const snapshot = mapSnapshot(doc);

    const locations = leafLocationIds.map((id, index) => {
      const location = locationDocs[index];
      const localizedName = location?.[`name_${locale}`] as string | undefined;
      return {
        id,
        name: localizedName || location?.name_en || doc.location_names[index] || "",
        type: doc.location_types?.[index] ?? "onsite",
        geoType: location?.type || doc.location_geo_types?.[index] || undefined,
        parentName: location?.parent_name || undefined,
      };
    });

    const technologies = (doc.technology_ids ?? []).map((id, index) => ({
      id,
      name: doc.technology_names?.[index] ?? "",
    })).filter((technology) => technology.name !== "");

    const descriptionLocale = doc.locales?.find((value) => value !== "_none") ?? "en";

    return {
      ...snapshot,
      company: {
        id: company?.id ?? snapshot.company.id,
        name: company?.name ?? snapshot.company.name,
        slug: company?.slug ?? snapshot.company.slug,
        logo: company?.logo ?? null,
        icon: company?.icon ?? snapshot.company.icon,
      },
      locations,
      employmentType: doc.employment_type || null,
      experienceMin: normalizeExperience(
        doc.experience_min_years ?? doc.experience_min,
        false,
      ),
      experienceMax: normalizeExperience(
        doc.experience_max_years ?? doc.experience_max,
        true,
      ),
      technologies,
      seniority: doc.seniority_id != null
        ? {
            id: doc.seniority_id,
            slug: seniority?.slug ?? "",
            name: seniority?.name ?? doc.seniority_name ?? "",
          }
        : null,
      descriptionLocale,
    };
  } catch (error) {
    logExternalError("error", { service: "typesense", operation: "posting_detail" }, error);
    return null;
  }
}
