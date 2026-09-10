import {
  EMPLOYMENT_TYPE_VALUES,
  WORK_MODE_VALUES,
  type WorkMode,
} from "@/lib/search/types";

export const WATCHLIST_TITLE_MAX_LENGTH = 100;
export const WATCHLIST_DESCRIPTION_MAX_LENGTH = 1_000;
export const WATCHLIST_HANDOFF_COMPANY_MAX = 25;
export const WATCHLIST_COMPANY_MAX = 250;

const KEYWORD_MAX_COUNT = 12;
const KEYWORD_MAX_LENGTH = 120;
const TAXONOMY_MAX_COUNT = 20;
const TAXONOMY_SLUG_MAX_LENGTH = 100;
const FILTER_STRING_BUDGET = 12_000;
const COMPANY_ID_MAX_LENGTH = 128;
const COMPANY_SLUG_MAX_LENGTH = 100;
const CURRENCY_LENGTH = 3;
const SALARY_MAX = 1_000_000_000;
const EXPERIENCE_MAX = 15;

const TITLE_CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/;
const TEXT_CONTROL_CHARACTERS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;
const TAXONOMY_SLUG = /^[\p{L}\p{N}][\p{L}\p{N}._-]*$/u;
const COMPANY_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const CURRENCY = /^[A-Z]{3}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type WatchlistFilters = {
  keywords?: string[];
  locationSlugs?: string[];
  occupationSlugs?: string[];
  senioritySlugs?: string[];
  technologySlugs?: string[];
  workMode?: WorkMode[];
  employmentType?: string[];
  salaryMin?: number;
  salaryMax?: number;
  salaryCurrency?: string;
  experienceMin?: number;
  experienceMax?: number;
  anyCompany?: boolean;
};

export type NormalizedWatchlistCompany = {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
};

type Normalized<T> = { ok: true; value: T } | { ok: false };

type CreateWatchlistInput = {
  title: string;
  description?: string;
  companyIds: string[];
  filters?: WatchlistFilters;
  isPublic?: boolean;
};

type UpdateWatchlistInput = {
  watchlistId: string;
  title?: string;
  description?: string | null;
  companyIds?: string[];
  filters?: WatchlistFilters;
  isPublic?: boolean;
};

type HandoffWatchlistInput = {
  title: string;
  description?: string;
  companySlugs: string[];
  filters?: WatchlistFilters;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function boundedText(
  value: unknown,
  maxLength: number,
  options: { allowEmpty: boolean; title?: boolean; trim?: boolean },
): string | null {
  if (typeof value !== "string" || value.length > maxLength) return null;
  const normalized = options.title || options.trim ? value.trim() : value;
  if (!options.allowEmpty && normalized.length === 0) return null;
  const forbidden = options.title ? TITLE_CONTROL_CHARACTERS : TEXT_CONTROL_CHARACTERS;
  return forbidden.test(normalized) ? null : normalized;
}

function normalizeStringArrayForWrite(
  value: unknown,
  options: {
    maxCount: number;
    maxLength: number;
    pattern?: RegExp;
    caseInsensitive?: boolean;
    lowercase?: boolean;
  },
): Normalized<string[]> {
  if (!Array.isArray(value) || value.length > options.maxCount) return { ok: false };
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of value) {
    if (typeof item !== "string") return { ok: false };
    let normalized = item.trim();
    if (options.lowercase) normalized = normalized.toLowerCase();
    if (
      normalized.length === 0 ||
      normalized.length > options.maxLength ||
      TEXT_CONTROL_CHARACTERS.test(normalized) ||
      (options.pattern && !options.pattern.test(normalized))
    ) {
      return { ok: false };
    }
    const key = options.caseInsensitive ? normalized.toLowerCase() : normalized;
    if (!seen.has(key)) {
      seen.add(key);
      result.push(normalized);
    }
  }
  return { ok: true, value: result };
}

function normalizeStringArrayForRead(
  value: unknown,
  options: {
    maxCount: number;
    maxLength: number;
    allowed?: ReadonlySet<string>;
    pattern?: RegExp;
    caseInsensitive?: boolean;
  },
): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of value.slice(0, options.maxCount)) {
    if (typeof item !== "string") continue;
    const normalized = item.trim();
    if (
      normalized.length === 0 ||
      normalized.length > options.maxLength ||
      TEXT_CONTROL_CHARACTERS.test(normalized) ||
      (options.allowed && !options.allowed.has(normalized)) ||
      (options.pattern && !options.pattern.test(normalized))
    ) {
      continue;
    }
    const key = options.caseInsensitive ? normalized.toLowerCase() : normalized;
    if (!seen.has(key)) {
      seen.add(key);
      result.push(normalized);
    }
  }
  return result.length > 0 ? result : undefined;
}

function optionalFiniteRangeValue(
  value: unknown,
  max: number,
  integer: boolean,
): number | null | undefined {
  if (value === undefined) return undefined;
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > max ||
    (integer && !Number.isInteger(value))
  ) {
    return null;
  }
  return value;
}

function normalizeFiltersForWrite(value: unknown): Normalized<WatchlistFilters> {
  if (!isRecord(value)) return { ok: false };
  const filters: WatchlistFilters = {};
  let stringBudget = 0;

  const listFields = [
    ["keywords", KEYWORD_MAX_COUNT, KEYWORD_MAX_LENGTH, undefined, true],
    ["locationSlugs", TAXONOMY_MAX_COUNT, TAXONOMY_SLUG_MAX_LENGTH, TAXONOMY_SLUG, false],
    ["occupationSlugs", TAXONOMY_MAX_COUNT, TAXONOMY_SLUG_MAX_LENGTH, TAXONOMY_SLUG, false],
    ["senioritySlugs", TAXONOMY_MAX_COUNT, TAXONOMY_SLUG_MAX_LENGTH, TAXONOMY_SLUG, false],
    ["technologySlugs", TAXONOMY_MAX_COUNT, TAXONOMY_SLUG_MAX_LENGTH, TAXONOMY_SLUG, false],
  ] as const;
  for (const [field, maxCount, maxLength, pattern, caseInsensitive] of listFields) {
    if (value[field] === undefined) continue;
    const normalized = normalizeStringArrayForWrite(value[field], {
      maxCount,
      maxLength,
      pattern,
      caseInsensitive,
    });
    if (!normalized.ok) return normalized;
    stringBudget += normalized.value.reduce((total, item) => total + item.length, 0);
    if (normalized.value.length > 0) filters[field] = normalized.value;
  }

  if (value.workMode !== undefined) {
    const normalized = normalizeStringArrayForWrite(value.workMode, {
      maxCount: WORK_MODE_VALUES.length,
      maxLength: 8,
      lowercase: true,
    });
    if (!normalized.ok || normalized.value.some((item) => !WORK_MODE_SET.has(item))) {
      return { ok: false };
    }
    if (normalized.value.length > 0) filters.workMode = normalized.value as WorkMode[];
  }
  if (value.employmentType !== undefined) {
    const normalized = normalizeStringArrayForWrite(value.employmentType, {
      maxCount: EMPLOYMENT_TYPE_VALUES.length,
      maxLength: 16,
      lowercase: true,
    });
    if (!normalized.ok || normalized.value.some((item) => !EMPLOYMENT_TYPE_SET.has(item))) {
      return { ok: false };
    }
    if (normalized.value.length > 0) {
      filters.employmentType = normalized.value;
    }
  }
  if (stringBudget > FILTER_STRING_BUDGET) return { ok: false };

  if (value.salaryCurrency !== undefined) {
    if (typeof value.salaryCurrency !== "string") return { ok: false };
    const currency = value.salaryCurrency.trim().toUpperCase();
    if (currency.length !== CURRENCY_LENGTH || !CURRENCY.test(currency)) return { ok: false };
    filters.salaryCurrency = currency;
  }

  const salaryMin = optionalFiniteRangeValue(value.salaryMin, SALARY_MAX, false);
  const salaryMax = optionalFiniteRangeValue(value.salaryMax, SALARY_MAX, false);
  const experienceMin = optionalFiniteRangeValue(value.experienceMin, EXPERIENCE_MAX, true);
  const experienceMax = optionalFiniteRangeValue(value.experienceMax, EXPERIENCE_MAX, true);
  if (
    salaryMin === null ||
    salaryMax === null ||
    experienceMin === null ||
    experienceMax === null
  ) {
    return { ok: false };
  }
  if (salaryMin !== undefined) filters.salaryMin = salaryMin;
  if (salaryMax !== undefined) filters.salaryMax = salaryMax;
  if (experienceMin !== undefined) filters.experienceMin = experienceMin;
  if (experienceMax !== undefined) filters.experienceMax = experienceMax;
  if (
    (salaryMin !== undefined && salaryMax !== undefined && salaryMin > salaryMax) ||
    (experienceMin !== undefined && experienceMax !== undefined && experienceMin > experienceMax)
  ) {
    return { ok: false };
  }

  if (value.anyCompany !== undefined) {
    if (typeof value.anyCompany !== "boolean") return { ok: false };
    filters.anyCompany = value.anyCompany;
  }

  return { ok: true, value: filters };
}

const WORK_MODE_SET: ReadonlySet<string> = new Set(WORK_MODE_VALUES);
const EMPLOYMENT_TYPE_SET: ReadonlySet<string> = new Set(EMPLOYMENT_TYPE_VALUES);

function normalizeOptionalFilters(value: unknown): Normalized<WatchlistFilters | undefined> {
  if (value === undefined) return { ok: true, value: undefined };
  return normalizeFiltersForWrite(value);
}

function normalizeOptionalDescription(value: unknown): Normalized<string | undefined> {
  if (value === undefined) return { ok: true, value: undefined };
  const description = boundedText(value, WATCHLIST_DESCRIPTION_MAX_LENGTH, { allowEmpty: true });
  return description === null ? { ok: false } : { ok: true, value: description };
}

function normalizeOptionalVisibility(value: unknown): Normalized<boolean | undefined> {
  return value === undefined || typeof value === "boolean"
    ? { ok: true, value: value as boolean | undefined }
    : { ok: false };
}

export function normalizeCreateWatchlistInput(value: unknown): Normalized<CreateWatchlistInput> {
  if (!isRecord(value)) return { ok: false };
  const title = boundedText(value.title, WATCHLIST_TITLE_MAX_LENGTH, {
    allowEmpty: false,
    title: true,
  });
  const description = normalizeOptionalDescription(value.description);
  const companyIds = normalizeStringArrayForWrite(value.companyIds, {
    maxCount: WATCHLIST_COMPANY_MAX,
    maxLength: COMPANY_ID_MAX_LENGTH,
    pattern: UUID,
    lowercase: true,
  });
  const filters = normalizeOptionalFilters(value.filters);
  const visibility = normalizeOptionalVisibility(value.isPublic);
  if (title === null || !description.ok || !companyIds.ok || !filters.ok || !visibility.ok) {
    return { ok: false };
  }
  return {
    ok: true,
    value: {
      title,
      description: description.value,
      companyIds: companyIds.value,
      filters: filters.value,
      isPublic: visibility.value,
    },
  };
}

export function normalizeUpdateWatchlistInput(value: unknown): Normalized<UpdateWatchlistInput> {
  if (!isRecord(value)) return { ok: false };
  const watchlistId = boundedText(value.watchlistId, COMPANY_ID_MAX_LENGTH, {
    allowEmpty: false,
    trim: true,
  });
  if (watchlistId === null || !UUID.test(watchlistId)) return { ok: false };

  let title: string | undefined;
  if (value.title !== undefined) {
    const normalized = boundedText(value.title, WATCHLIST_TITLE_MAX_LENGTH, {
      allowEmpty: false,
      title: true,
    });
    if (normalized === null) return { ok: false };
    title = normalized;
  }

  let description: string | null | undefined;
  if (value.description === null) {
    description = null;
  } else {
    const normalized = normalizeOptionalDescription(value.description);
    if (!normalized.ok) return { ok: false };
    description = normalized.value;
  }

  let companyIds: string[] | undefined;
  if (value.companyIds !== undefined) {
    const normalized = normalizeStringArrayForWrite(value.companyIds, {
      maxCount: WATCHLIST_COMPANY_MAX,
      maxLength: COMPANY_ID_MAX_LENGTH,
      pattern: UUID,
      lowercase: true,
    });
    if (!normalized.ok) return { ok: false };
    companyIds = normalized.value;
  }
  const filters = normalizeOptionalFilters(value.filters);
  const visibility = normalizeOptionalVisibility(value.isPublic);
  if (!filters.ok || !visibility.ok) return { ok: false };

  return {
    ok: true,
    value: {
      watchlistId,
      title,
      description,
      companyIds,
      filters: filters.value,
      isPublic: visibility.value,
    },
  };
}

export function normalizeHandoffWatchlistInput(value: unknown): Normalized<HandoffWatchlistInput> {
  if (!isRecord(value)) return { ok: false };
  const title = boundedText(value.title, WATCHLIST_TITLE_MAX_LENGTH, {
    allowEmpty: false,
    title: true,
  });
  const description = normalizeOptionalDescription(value.description);
  const companySlugs = normalizeStringArrayForWrite(value.companySlugs, {
    maxCount: WATCHLIST_HANDOFF_COMPANY_MAX,
    maxLength: COMPANY_SLUG_MAX_LENGTH,
    pattern: COMPANY_SLUG,
    lowercase: true,
  });
  const filters = normalizeOptionalFilters(value.filters);
  if (title === null || !description.ok || !companySlugs.ok || !filters.ok) {
    return { ok: false };
  }
  return {
    ok: true,
    value: {
      title,
      description: description.value,
      companySlugs: companySlugs.value,
      filters: filters.value,
    },
  };
}

/** Sanitize legacy or malformed JSONB without letting it reach Typesense/UI. */
export function normalizeWatchlistFiltersForRead(value: unknown): WatchlistFilters {
  if (!isRecord(value)) return {};
  const filters: WatchlistFilters = {};
  const keywords = normalizeStringArrayForRead(value.keywords, {
    maxCount: KEYWORD_MAX_COUNT,
    maxLength: KEYWORD_MAX_LENGTH,
    caseInsensitive: true,
  });
  if (keywords) filters.keywords = keywords;
  for (const field of [
    "locationSlugs",
    "occupationSlugs",
    "senioritySlugs",
    "technologySlugs",
  ] as const) {
    const normalized = normalizeStringArrayForRead(value[field], {
      maxCount: TAXONOMY_MAX_COUNT,
      maxLength: TAXONOMY_SLUG_MAX_LENGTH,
      pattern: TAXONOMY_SLUG,
    });
    if (normalized) filters[field] = normalized;
  }
  const workMode = normalizeStringArrayForRead(value.workMode, {
    maxCount: WORK_MODE_VALUES.length,
    maxLength: 8,
    allowed: WORK_MODE_SET,
  });
  if (workMode) filters.workMode = workMode as WorkMode[];
  const employmentType = normalizeStringArrayForRead(value.employmentType, {
    maxCount: EMPLOYMENT_TYPE_VALUES.length,
    maxLength: 16,
    allowed: EMPLOYMENT_TYPE_SET,
  });
  if (employmentType) filters.employmentType = employmentType;

  if (typeof value.salaryCurrency === "string") {
    const currency = value.salaryCurrency.trim().toUpperCase();
    if (CURRENCY.test(currency)) filters.salaryCurrency = currency;
  }
  const salaryMin = optionalFiniteRangeValue(value.salaryMin, SALARY_MAX, false);
  const salaryMax = optionalFiniteRangeValue(value.salaryMax, SALARY_MAX, false);
  if (salaryMin !== null && salaryMin !== undefined) filters.salaryMin = salaryMin;
  if (salaryMax !== null && salaryMax !== undefined) filters.salaryMax = salaryMax;
  if (filters.salaryMin !== undefined && filters.salaryMax !== undefined && filters.salaryMin > filters.salaryMax) {
    delete filters.salaryMin;
    delete filters.salaryMax;
  }
  const experienceMin = optionalFiniteRangeValue(value.experienceMin, EXPERIENCE_MAX, true);
  const experienceMax = optionalFiniteRangeValue(value.experienceMax, EXPERIENCE_MAX, true);
  if (experienceMin !== null && experienceMin !== undefined) filters.experienceMin = experienceMin;
  if (experienceMax !== null && experienceMax !== undefined) filters.experienceMax = experienceMax;
  if (
    filters.experienceMin !== undefined &&
    filters.experienceMax !== undefined &&
    filters.experienceMin > filters.experienceMax
  ) {
    delete filters.experienceMin;
    delete filters.experienceMax;
  }
  if (typeof value.anyCompany === "boolean") filters.anyCompany = value.anyCompany;
  return filters;
}

/** Shared capability URLs fail closed rather than silently changing filters. */
export function normalizeWatchlistFiltersForSharedRead(
  value: unknown,
): WatchlistFilters | null {
  const normalized = normalizeFiltersForWrite(value);
  return normalized.ok ? normalized.value : null;
}

export function normalizeSharedWatchlistMetadata(value: {
  title: unknown;
  description: unknown;
}): { title: string; description: string | null } | null {
  const title = boundedText(value.title, WATCHLIST_TITLE_MAX_LENGTH, {
    allowEmpty: false,
    title: true,
  });
  if (title === null) return null;
  if (value.description === null) return { title, description: null };
  const description = boundedText(value.description, WATCHLIST_DESCRIPTION_MAX_LENGTH, {
    allowEmpty: true,
  });
  return description === null ? null : { title, description };
}

export function normalizeWatchlistCompanyIdsForRead(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return normalizeStringArrayForRead(value, {
    maxCount: value.length,
    maxLength: COMPANY_ID_MAX_LENGTH,
  }) ?? [];
}

export function normalizeWatchlistUuid(value: unknown): string | null {
  const normalized = boundedText(value, COMPANY_ID_MAX_LENGTH, {
    allowEmpty: false,
    trim: true,
  });
  return normalized !== null && UUID.test(normalized) ? normalized.toLowerCase() : null;
}

export function normalizeWatchlistCompaniesForRead(
  value: unknown,
  maxCount?: number,
): NormalizedWatchlistCompany[] | null {
  if (!Array.isArray(value)) return [];
  if (maxCount !== undefined && value.length > maxCount) return null;
  const result: NormalizedWatchlistCompany[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    if (!isRecord(item)) continue;
    const id = boundedText(item.id, COMPANY_ID_MAX_LENGTH, { allowEmpty: false, trim: true });
    const name = boundedText(item.name, 300, { allowEmpty: false, trim: true });
    const slug = boundedText(item.slug, COMPANY_SLUG_MAX_LENGTH, { allowEmpty: false, trim: true });
    if (id === null || name === null || slug === null || !COMPANY_SLUG.test(slug)) continue;
    const icon = typeof item.icon === "string"
      ? boundedText(item.icon, 2_048, { allowEmpty: false, trim: true })
      : null;
    if (seen.has(id)) continue;
    seen.add(id);
    result.push({ id, name, slug, icon });
  }
  return result;
}
