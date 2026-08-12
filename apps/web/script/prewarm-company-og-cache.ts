/**
 * Pre-render Open Graph cards outside Vercel Functions.
 *
 * The script uploads the deterministic site-wide fallback card and reads the
 * versioned company sources that feed production to fill any missing objects
 * in the current company renderer-version namespace.
 */
import {
  ListObjectsV2Command,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { parse } from "csv-parse/sync";
import {
  logExternalError,
  safeExternalError,
  type ExternalService,
  type SafeExternalError,
} from "@/lib/safe-external-error";
import { mapTypesenseCompanyHitToDetail } from "@/lib/services/company-detail-lookup";
import { renderCompanyOgCard } from "@/lib/og/company-og-card";
import { renderSiteOgCard } from "@/lib/og/site-og-card";
import {
  companyOgCacheKeyForVersion,
  companyOgCompletionKeyForVersion,
  companyOgCurrentCompletionKey,
} from "@/lib/og/company-og-key";
import { computeCompanyOgRendererVersion } from "@/lib/og/company-og-renderer-version";
import { computeCompanyOgSourceVersion } from "@/lib/og/company-og-source-version";
import { SITE_OG_KEY } from "@/lib/og/site-og-key";

const ALL_LOCALES = ["en", "de", "fr", "it"] as const;
const CONTENT_TYPE = "image/png";
const CACHE_CONTROL = "public, max-age=31536000, immutable";
const CURRENT_MARKER_CACHE_CONTROL =
  "public, max-age=60, stale-while-revalidate=240";
const MARKER_CONTENT_TYPE = "application/json";
const COMPANY_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const COMPANY_DATA_DIR = resolve(process.cwd(), "../crawler/data");

class PrewarmExternalError extends Error {
  constructor(
    readonly service: ExternalService,
    readonly operation: string,
    readonly externalCause: unknown,
  ) {
    super("Company OG prewarm external dependency failed");
  }
}

type Options = {
  concurrency: number;
  force: boolean;
  locales: string[];
  maxCompanies: number | null;
  rendererVersion: string | null;
  yes: boolean;
};

type RenderTask = {
  company: Record<string, unknown>;
  locale: string;
  slug: string;
};

function usage(): string {
  return [
    "Usage: pnpm --filter @jobseek/web og:prewarm -- --yes [options]",
    "",
    "Options:",
    "  --yes                       Confirm writes to R2 (required).",
    "  --concurrency <n>           Parallel render/upload workers. Default: 4.",
    "  --locales <csv>             Locale subset. Default: en,de,fr,it.",
    "  --max-companies <n>         Bound company count for a canary run.",
    "  --renderer-version <value>  Override the computed namespace.",
    "  --force                     Overwrite objects that already exist.",
  ].join("\n");
}

function positiveInteger(value: string, flag: string): number {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isSafeInteger(parsed) || parsed < 1) {
    throw new Error(`${flag} must be a positive integer`);
  }
  return parsed;
}

export function parseOptions(argv: string[]): Options {
  const options: Options = {
    concurrency: 4,
    force: false,
    locales: [...ALL_LOCALES],
    maxCompanies: null,
    rendererVersion: null,
    yes: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--") {
      continue;
    } else if (argument === "--yes") {
      options.yes = true;
    } else if (argument === "--force") {
      options.force = true;
    } else if (argument === "--concurrency") {
      options.concurrency = positiveInteger(argv[++index] ?? "", argument);
    } else if (argument === "--max-companies") {
      options.maxCompanies = positiveInteger(argv[++index] ?? "", argument);
    } else if (argument === "--renderer-version") {
      options.rendererVersion = argv[++index] ?? "";
      if (!options.rendererVersion) throw new Error(`${argument} requires a value`);
    } else if (argument === "--locales") {
      const locales = (argv[++index] ?? "").split(",").filter(Boolean);
      if (locales.length === 0 || locales.some((locale) => !ALL_LOCALES.includes(
        locale as (typeof ALL_LOCALES)[number],
      ))) {
        throw new Error(`${argument} must contain only: ${ALL_LOCALES.join(",")}`);
      }
      options.locales = [...new Set(locales)];
    } else if (argument === "--help" || argument === "-h") {
      console.log(usage());
      process.exit(0);
    } else {
      throw new Error(`Unknown option: ${argument}\n\n${usage()}`);
    }
  }

  return options;
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function createR2Client(): { client: S3Client; bucket: string } {
  const endpoint = requiredEnvironment("R2_ENDPOINT_URL");
  const accessKeyId = requiredEnvironment("R2_ACCESS_KEY_ID");
  const secretAccessKey = requiredEnvironment("R2_SECRET_ACCESS_KEY");
  const bucket = requiredEnvironment("R2_BUCKET");
  return {
    bucket,
    client: new S3Client({
      endpoint,
      region: "auto",
      forcePathStyle: true,
      credentials: { accessKeyId, secretAccessKey },
    }),
  };
}

function parseCsv(source: string, label: string): Record<string, string>[] {
  const rows = parse(source, {
    bom: true,
    columns: true,
    relax_column_count: false,
    skip_empty_lines: true,
  }) as Record<string, string>[];
  if (rows.length === 0) throw new Error(`${label} must contain at least one row`);
  return rows;
}

function value(row: Record<string, string>, field: string): string | null {
  const candidate = row[field]?.trim();
  return candidate ? candidate : null;
}

function optionalInteger(
  row: Record<string, string>,
  field: string,
  context: string,
): number | null {
  const candidate = value(row, field);
  if (!candidate) return null;
  const parsed = Number.parseInt(candidate, 10);
  if (!Number.isSafeInteger(parsed) || String(parsed) !== candidate) {
    throw new Error(`${context}.${field} must be an integer`);
  }
  return parsed;
}

/** Build the Typesense-compatible records consumed by the shared card mapper. */
export function buildCompanyDocuments(
  companiesSource: string,
  descriptionsSource: string,
  industriesSource: string,
  maxCompanies: number | null,
): Record<string, unknown>[] {
  const companyRows = parseCsv(companiesSource, "companies.csv");
  const descriptionRows = parseCsv(descriptionsSource, "company_descriptions.csv");
  const industryRows = parseCsv(industriesSource, "industries.csv");

  const industries = new Map<number, string>();
  for (const row of industryRows) {
    const id = optionalInteger(row, "id", "industry");
    const name = value(row, "en") ?? value(row, "name");
    if (id === null || id < 1 || !name) {
      throw new Error("Every industry must have a positive id and a name");
    }
    if (industries.has(id)) throw new Error(`Duplicate industry id: ${id}`);
    industries.set(id, name);
  }

  const descriptions = new Map<string, Record<string, string>>();
  for (const row of descriptionRows) {
    const slug = value(row, "slug");
    if (!slug || !COMPANY_SLUG.test(slug)) {
      throw new Error("Every company description must have a valid slug");
    }
    if (descriptions.has(slug)) {
      throw new Error(`Duplicate company description slug: ${slug}`);
    }
    descriptions.set(slug, row);
  }

  const seenSlugs = new Set<string>();
  const documents = companyRows.map((row) => {
    const slug = value(row, "slug");
    const name = value(row, "name");
    if (!slug || !COMPANY_SLUG.test(slug) || !name) {
      throw new Error("Every company must have a valid slug and name");
    }
    if (seenSlugs.has(slug)) throw new Error(`Duplicate company slug: ${slug}`);
    seenSlugs.add(slug);

    const industryId = optionalInteger(row, "industry", `company.${slug}`);
    const industryName = industryId === null ? null : industries.get(industryId);
    if (industryId !== null && !industryName) {
      throw new Error(`company.${slug}.industry references missing id ${industryId}`);
    }

    const document: Record<string, unknown> = {
      id: slug,
      name,
      slug,
      active_posting_count: 0,
    };
    const directFields = [
      ["icon", "icon_url"],
      ["logo", "logo_url"],
      ["website", "website"],
    ] as const;
    for (const [target, source] of directFields) {
      const fieldValue = value(row, source);
      if (fieldValue) document[target] = fieldValue;
    }

    if (industryId !== null) document.industry_id = industryId;
    if (industryName) document.industry_name = industryName;
    for (const field of ["employee_count_range", "founded_year"] as const) {
      const fieldValue = optionalInteger(row, field, `company.${slug}`);
      if (fieldValue !== null) document[field] = fieldValue;
    }

    const localizedDescriptions = descriptions.get(slug);
    for (const locale of ALL_LOCALES) {
      const description = localizedDescriptions
        ? value(localizedDescriptions, locale)
        : null;
      if (!description) continue;
      document[locale === "en" ? "description" : `description_${locale}`] =
        description;
    }

    return document;
  });

  return maxCompanies ? documents.slice(0, maxCompanies) : documents;
}

function listCompanies(maxCompanies: number | null): Record<string, unknown>[] {
  return buildCompanyDocuments(
    readFileSync(resolve(COMPANY_DATA_DIR, "companies.csv"), "utf8"),
    readFileSync(resolve(COMPANY_DATA_DIR, "company_descriptions.csv"), "utf8"),
    readFileSync(resolve(COMPANY_DATA_DIR, "industries.csv"), "utf8"),
    maxCompanies,
  );
}

async function listExistingKeys(
  client: S3Client,
  bucket: string,
  prefix: string,
): Promise<Set<string>> {
  const keys = new Set<string>();
  let continuationToken: string | undefined;

  do {
    const response = await client.send(new ListObjectsV2Command({
      Bucket: bucket,
      Prefix: prefix,
      ContinuationToken: continuationToken,
      MaxKeys: 1000,
    }));
    for (const object of response.Contents ?? []) {
      if (object.Key) keys.add(object.Key);
    }
    continuationToken = response.IsTruncated
      ? response.NextContinuationToken
      : undefined;
    if (response.IsTruncated && !continuationToken) {
      throw new Error("R2 returned a truncated object listing without a continuation token");
    }
  } while (continuationToken);

  return keys;
}

function isPng(bytes: Uint8Array): boolean {
  return (
    bytes.length > 8 &&
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47
  );
}

async function renderAndUploadSiteOg(
  client: S3Client,
  bucket: string,
): Promise<void> {
  const response = await renderSiteOgCard();
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!isPng(bytes)) {
    throw new Error("Site OG renderer did not produce PNG bytes");
  }

  await client.send(new PutObjectCommand({
    Bucket: bucket,
    Key: SITE_OG_KEY,
    Body: bytes,
    ContentType: CONTENT_TYPE,
    CacheControl: CACHE_CONTROL,
  }));
}

export async function prewarmSiteOgCard(
  client: S3Client,
  bucket: string,
  force: boolean,
): Promise<"existing" | "uploaded"> {
  if (!force) {
    const existing = await listExistingKeys(client, bucket, "og/site/");
    if (existing.has(SITE_OG_KEY)) return "existing";
  }

  await withRetry(() => renderAndUploadSiteOg(client, bucket));
  return "uploaded";
}

async function renderAndUpload(
  client: S3Client,
  bucket: string,
  rendererVersion: string,
  task: RenderTask,
): Promise<string> {
  const company = mapTypesenseCompanyHitToDetail(
    task.company,
    task.slug,
    task.locale,
  );
  const response = renderCompanyOgCard(company);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!isPng(bytes)) {
    throw new Error(`Renderer did not produce PNG bytes for ${task.locale}/${task.slug}`);
  }

  const key = companyOgCacheKeyForVersion(
    rendererVersion,
    task.locale,
    task.slug,
  );
  await client.send(new PutObjectCommand({
    Bucket: bucket,
    Key: key,
    Body: bytes,
    ContentType: CONTENT_TYPE,
    CacheControl: CACHE_CONTROL,
  }));
  return key;
}

export async function withRetry<T>(
  operation: () => Promise<T>,
  baseDelayMs = 250,
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      if (attempt < 2) await delay(baseDelayMs * 2 ** attempt);
    }
  }
  throw lastError;
}

export async function main() {
  const options = parseOptions(process.argv.slice(2));
  if (!options.yes) throw new Error(`R2 writes require --yes.\n\n${usage()}`);

  const rendererVersion = options.rendererVersion ??
    computeCompanyOgRendererVersion(process.cwd());
  const sourceVersion = computeCompanyOgSourceVersion(process.cwd());
  const prefix = `og/company/${rendererVersion}/`;
  const { client, bucket } = createR2Client();
  const siteOg = await prewarmSiteOgCard(client, bucket, options.force).catch(
    (error) => {
      throw new PrewarmExternalError("r2", "prewarm_site_og", error);
    },
  );
  const companies = listCompanies(options.maxCompanies);
  const existingKeys = options.force
    ? new Set<string>()
    : await listExistingKeys(client, bucket, prefix).catch((error) => {
        throw new PrewarmExternalError(
          "r2",
          "list_company_og_cache",
          error,
        );
      });

  const tasks: RenderTask[] = [];
  let skipped = 0;
  for (const company of companies) {
    const slug = typeof company.slug === "string" ? company.slug : "";
    if (!slug) throw new Error(`Company document ${String(company.id)} has no slug`);
    for (const locale of options.locales) {
      const key = companyOgCacheKeyForVersion(rendererVersion, locale, slug);
      if (existingKeys.has(key)) {
        skipped += 1;
      } else {
        tasks.push({ company, locale, slug });
      }
    }
  }

  console.log(JSON.stringify({
    event: "company_og_prewarm_started",
    rendererVersion,
    sourceVersion,
    companies: companies.length,
    locales: options.locales,
    existing: skipped,
    pending: tasks.length,
    concurrency: options.concurrency,
    force: options.force,
    siteOg,
  }));

  let cursor = 0;
  let uploaded = 0;
  const failures: Array<{ key: string; error: SafeExternalError }> = [];

  const worker = async () => {
    while (true) {
      const index = cursor;
      cursor += 1;
      const task = tasks[index];
      if (!task) return;
      const key = companyOgCacheKeyForVersion(
        rendererVersion,
        task.locale,
        task.slug,
      );
      try {
        await withRetry(() => renderAndUpload(
          client,
          bucket,
          rendererVersion,
          task,
        ));
        uploaded += 1;
        if (uploaded % 100 === 0 || uploaded === tasks.length) {
          console.log(JSON.stringify({
            event: "company_og_prewarm_progress",
            rendererVersion,
            uploaded,
            total: tasks.length,
          }));
        }
      } catch (error) {
        failures.push({
          key,
          error: safeExternalError(error, {
            service: "r2",
            operation: "upload_company_og",
            retryCount: 3,
          }),
        });
      }
    }
  };

  await Promise.all(
    Array.from(
      { length: Math.min(options.concurrency, Math.max(tasks.length, 1)) },
      () => worker(),
    ),
  );

  let completionMarker: string | null = null;
  let currentMarker: string | null = null;
  const isFullMatrix = options.maxCompanies === null &&
    options.locales.length === ALL_LOCALES.length &&
    ALL_LOCALES.every((locale) => options.locales.includes(locale));
  if (failures.length === 0 && isFullMatrix) {
    const key = companyOgCompletionKeyForVersion(
      rendererVersion,
      sourceVersion,
    );
    let markerKey = key;
    try {
      await withRetry(() => client.send(new PutObjectCommand({
        Bucket: bucket,
        Key: key,
        Body: JSON.stringify({
          complete: true,
          rendererVersion,
          sourceVersion,
          companies: companies.length,
          locales: options.locales,
          expected: companies.length * options.locales.length,
          completedAt: new Date().toISOString(),
        }),
        ContentType: MARKER_CONTENT_TYPE,
        CacheControl: CACHE_CONTROL,
      })));
      completionMarker = key;

      const currentKey = companyOgCurrentCompletionKey(rendererVersion);
      markerKey = currentKey;
      await withRetry(() => client.send(new PutObjectCommand({
        Bucket: bucket,
        Key: currentKey,
        Body: JSON.stringify({
          complete: true,
          rendererVersion,
          sourceVersion,
          companies: companies.length,
          locales: options.locales,
          expected: companies.length * options.locales.length,
          completedAt: new Date().toISOString(),
        }),
        ContentType: MARKER_CONTENT_TYPE,
        CacheControl: CURRENT_MARKER_CACHE_CONTROL,
      })));
      currentMarker = currentKey;
    } catch (error) {
      failures.push({
        key: markerKey,
        error: safeExternalError(error, {
          service: "r2",
          operation: "publish_company_og_completion",
          retryCount: 3,
        }),
      });
    }
  }

  console.log(JSON.stringify({
    event: "company_og_prewarm_completed",
    rendererVersion,
    sourceVersion,
    companies: companies.length,
    expected: companies.length * options.locales.length,
    skipped,
    uploaded,
    failed: failures.length,
    completionMarker,
    currentMarker,
    siteOg,
  }));

  if (failures.length > 0) {
    console.error(JSON.stringify({
      event: "company_og_prewarm_failures",
      failures: failures.slice(0, 50),
    }));
    process.exitCode = 1;
  }
}

const entrypoint = process.argv[1] ?? "";
if (/prewarm-company-og-cache\.(?:ts|js|mjs|cjs)$/.test(entrypoint)) {
  void main().catch((error) => {
    if (error instanceof PrewarmExternalError) {
      logExternalError(
        "error",
        { service: error.service, operation: error.operation },
        error.externalCause,
      );
    } else {
      console.error("company_og_prewarm_failed", { kind: "configuration_or_internal" });
    }
    process.exitCode = 1;
  });
}
