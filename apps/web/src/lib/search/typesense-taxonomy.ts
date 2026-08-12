import "server-only";

import { getTypesenseClient, type TypesenseHit } from "@/lib/search/typesense-client";
import { sanitizeTypesenseBoundaryError } from "@/lib/search/typesense-retry";

const PAGE_SIZE = 250;
const FILTER_BATCH_SIZE = 100;

type SearchParams = Record<string, string | number | boolean>;

export type TypesenseLocationDocument = {
  id: string;
  location_id: number;
  slug: string;
  name_en: string;
  name_de?: string;
  name_fr?: string;
  name_it?: string;
  aliases?: string[];
  parent_id?: number;
  parent_name?: string;
  ancestor_ids?: number[];
  member_country_ids?: number[];
  type: string;
  active_posting_count: number;
};

export type TypesenseOccupationDocument = {
  id: string;
  occupation_id: number;
  slug: string;
  name: string;
  parent_id?: number;
  domain_id?: number;
  domain_slug?: string;
  domain_name?: string;
  locale: string;
};

export type TypesenseSeniorityDocument = {
  id: string;
  seniority_id: number;
  slug: string;
  name: string;
  locale: string;
};

export type TypesenseTechnologyDocument = {
  id: string;
  technology_id: number;
  slug: string;
  name: string;
  category?: string;
};

function chunks<T>(values: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < values.length; i += size) out.push(values.slice(i, i + size));
  return out;
}

function filterLiteral(value: string): string {
  return `\`${value.replace(/\\/g, "\\\\").replace(/`/g, "\\`")}\``;
}

async function searchAll<T>(
  collection: string,
  params: SearchParams,
): Promise<T[]> {
  const client = getTypesenseClient();
  const documents: T[] = [];

  for (let page = 1; ; page += 1) {
    let response;
    try {
      response = await client.collections(collection).documents().search({
        ...params,
        page,
        per_page: PAGE_SIZE,
      });
    } catch (err) {
      throw sanitizeTypesenseBoundaryError(err);
    }
    const hits = (response.hits ?? []) as unknown as TypesenseHit[];
    documents.push(...hits.map((hit) => hit.document as T));
    if (hits.length < PAGE_SIZE || documents.length >= (response.found ?? 0)) break;
  }

  return documents;
}

export async function fetchLocationDocumentsByIds(
  ids: number[],
): Promise<TypesenseLocationDocument[]> {
  const unique = [...new Set(ids)].filter(Number.isInteger);
  const results = await Promise.all(
    chunks(unique, FILTER_BATCH_SIZE).map((batch) =>
      searchAll<TypesenseLocationDocument>("location", {
        q: "*",
        query_by: "name_en",
        filter_by: `id:[${batch.join(",")}]`,
      }),
    ),
  );
  return results.flat();
}

export async function fetchLocationDocumentsWithAncestors(
  ids: number[],
): Promise<TypesenseLocationDocument[]> {
  const byId = new Map<number, TypesenseLocationDocument>();
  let pending = [...new Set(ids)].sort((a, b) => a - b);
  while (pending.length > 0) {
    const rows = await fetchLocationDocumentsByIds(pending);
    for (const row of rows) byId.set(row.location_id, row);
    pending = [
      ...new Set(
        rows.flatMap((row) =>
          row.parent_id != null && !byId.has(row.parent_id) ? [row.parent_id] : [],
        ),
      ),
    ].sort((a, b) => a - b);
  }
  return [...byId.values()];
}

export async function fetchLocationDocumentsBySlugs(
  slugs: string[],
): Promise<TypesenseLocationDocument[]> {
  const unique = [...new Set(slugs)];
  const results = await Promise.all(
    chunks(unique, FILTER_BATCH_SIZE).map((batch) =>
      searchAll<TypesenseLocationDocument>("location", {
        q: "*",
        query_by: "name_en",
        filter_by: `slug:[${batch.map(filterLiteral).join(",")}]`,
      }),
    ),
  );
  return results.flat();
}

export function fetchLocationMacroDocuments(): Promise<TypesenseLocationDocument[]> {
  return searchAll<TypesenseLocationDocument>("location", {
    q: "*",
    query_by: "name_en",
    filter_by: "type:=macro",
  });
}

export async function fetchLocationDescendants(
  ancestorIds: number[],
): Promise<TypesenseLocationDocument[]> {
  const unique = [...new Set(ancestorIds)].filter(Number.isInteger);
  const results = await Promise.all(
    chunks(unique, FILTER_BATCH_SIZE).map((batch) =>
      searchAll<TypesenseLocationDocument>("location", {
        q: "*",
        query_by: "name_en",
        filter_by: `ancestor_ids:[${batch.join(",")}]`,
      }),
    ),
  );
  const byId = new Map<number, TypesenseLocationDocument>();
  for (const doc of results.flat()) byId.set(doc.location_id, doc);
  return [...byId.values()];
}

function safeLocale(locale: string): string {
  return /^[a-z]{2}$/i.test(locale) ? locale.toLowerCase() : "en";
}

async function fetchLocalizedDocuments<T extends { locale: string }>(
  collection: "occupation" | "seniority",
  locale: string,
): Promise<T[]> {
  const preferred = safeLocale(locale);
  const localeFilter = preferred === "en" ? "locale:=en" : `locale:[${preferred},en]`;
  return searchAll<T>(collection, {
    q: "*",
    query_by: "name",
    filter_by: localeFilter,
  });
}

function preferLocale<T extends { locale: string }>(
  documents: T[],
  locale: string,
  id: (document: T) => number,
): T[] {
  const preferred = safeLocale(locale);
  const byId = new Map<number, T>();
  for (const document of documents) {
    const current = byId.get(id(document));
    if (!current || (document.locale === preferred && current.locale !== preferred)) {
      byId.set(id(document), document);
    }
  }
  return [...byId.values()];
}

export async function fetchOccupationDocuments(
  locale: string,
): Promise<TypesenseOccupationDocument[]> {
  const documents = await fetchLocalizedDocuments<TypesenseOccupationDocument>(
    "occupation",
    locale,
  );
  return preferLocale(documents, locale, (document) => document.occupation_id);
}

export async function fetchSeniorityDocuments(
  locale: string,
): Promise<TypesenseSeniorityDocument[]> {
  const documents = await fetchLocalizedDocuments<TypesenseSeniorityDocument>(
    "seniority",
    locale,
  );
  return preferLocale(documents, locale, (document) => document.seniority_id);
}

export function fetchTechnologyDocuments(): Promise<TypesenseTechnologyDocument[]> {
  return searchAll<TypesenseTechnologyDocument>("technology", {
    q: "*",
    query_by: "name",
  });
}
