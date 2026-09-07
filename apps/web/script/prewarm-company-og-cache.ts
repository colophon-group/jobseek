/**
 * Pre-render Open Graph cards outside Vercel Functions.
 *
 * The script uploads the deterministic site-wide fallback card and reads the
 * versioned company sources that feed production to fill any missing objects
 * in the current company renderer-version namespace.
 */
import {
  GetObjectCommand,
  ListObjectsV2Command,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { spawnSync } from "node:child_process";
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
import {
  computeCompanyOgSourceVersionFromSources,
} from "@/lib/og/company-og-source-version";
import {
  COMPANY_OG_COMPLETION_SCHEMA_VERSION,
  parseCompanyOgCompletionMarker,
  sameCompanyOgCompletionCoverage,
  type CompanyOgCompletionMarker,
} from "@/lib/og/company-og-completion-marker";
import {
  planCompanyOgPrewarm,
  type CompanyOgDocument,
  type CompanyOgRenderTask,
} from "@/lib/og/company-og-prewarm-plan";
import { SITE_OG_KEY } from "@/lib/og/site-og-key";

const ALL_LOCALES = ["en", "de", "fr", "it"] as const;
const CONTENT_TYPE = "image/png";
const CACHE_CONTROL = "public, max-age=31536000, immutable";
const CURRENT_MARKER_CACHE_CONTROL =
  "public, max-age=60, stale-while-revalidate=240";
const MARKER_CONTENT_TYPE = "application/json";
const COMPANY_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const GIT_REVISION = /^[a-f0-9]{40}$/;
const COMPANY_DATA_PATHS = [
  "apps/crawler/data/companies.csv",
  "apps/crawler/data/company_descriptions.csv",
  "apps/crawler/data/industries.csv",
] as const;
const SOURCE_HASH_PATHS = [
  "../crawler/data/companies.csv",
  "../crawler/data/company_descriptions.csv",
  "../crawler/data/industries.csv",
] as const;
export const FULL_REBUILD_CONFIRMATION = "REBUILD-COMPANY-OG";

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
  fullRebuild: boolean;
  fullRebuildConfirmation: string | null;
  locales: string[];
  maxCompanies: number | null;
  maxPlannedWrites: number;
  maxPutAttempts: number;
  rendererVersion: string | null;
  targetRevision: string | null;
  yes: boolean;
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
    "  --target-revision <sha>      Exact 40-character Git revision to publish.",
    "  --max-planned-writes <n>    Reject a larger plan before any PUT. Default: 1000.",
    "  --max-put-attempts <n>      Runtime PUT-attempt ceiling. Default: 3000.",
    "  --full-rebuild              Manual-only namespace bootstrap/repair.",
    `  --approve-full-rebuild <s>  Required value: ${FULL_REBUILD_CONFIRMATION}.`,
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
    fullRebuild: false,
    fullRebuildConfirmation: null,
    locales: [...ALL_LOCALES],
    maxCompanies: null,
    maxPlannedWrites: 1000,
    maxPutAttempts: 3000,
    rendererVersion: null,
    targetRevision: null,
    yes: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--") {
      continue;
    } else if (argument === "--yes") {
      options.yes = true;
    } else if (argument === "--full-rebuild") {
      options.fullRebuild = true;
    } else if (argument === "--approve-full-rebuild") {
      options.fullRebuildConfirmation = argv[++index] ?? "";
    } else if (argument === "--concurrency") {
      options.concurrency = positiveInteger(argv[++index] ?? "", argument);
    } else if (argument === "--max-companies") {
      options.maxCompanies = positiveInteger(argv[++index] ?? "", argument);
    } else if (argument === "--renderer-version") {
      options.rendererVersion = argv[++index] ?? "";
      if (!/^[a-z0-9-]{1,120}$/.test(options.rendererVersion)) {
        throw new Error(`${argument} must contain only lowercase letters, digits, and hyphens`);
      }
    } else if (argument === "--target-revision") {
      options.targetRevision = argv[++index] ?? "";
      if (!GIT_REVISION.test(options.targetRevision)) {
        throw new Error(`${argument} must be an exact 40-character lowercase SHA`);
      }
    } else if (argument === "--max-planned-writes") {
      options.maxPlannedWrites = positiveInteger(argv[++index] ?? "", argument);
    } else if (argument === "--max-put-attempts") {
      options.maxPutAttempts = positiveInteger(argv[++index] ?? "", argument);
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

  if (
    options.fullRebuild &&
    options.fullRebuildConfirmation !== FULL_REBUILD_CONFIRMATION
  ) {
    throw new Error(
      `--full-rebuild requires --approve-full-rebuild ${FULL_REBUILD_CONFIRMATION}`,
    );
  }
  if (!options.fullRebuild && options.fullRebuildConfirmation !== null) {
    throw new Error("--approve-full-rebuild requires --full-rebuild");
  }
  if (options.concurrency > 4) {
    throw new Error("--concurrency must be between 1 and 4");
  }

  return options;
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

export function createR2Client(): { client: S3Client; bucket: string } {
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
      // Keep every physical request visible to PutAttemptBudget. The explicit
      // outer retry loop is the only retry layer allowed for R2 writes.
      maxAttempts: 1,
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
): CompanyOgDocument[] {
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

    const document: CompanyOgDocument = {
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

function runGit(rootDir: string, args: string[]): string {
  const result = spawnSync("git", args, {
    cwd: rootDir,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  if (result.status !== 0) {
    throw new Error(`git ${args[0]} failed while planning company OG prewarm`);
  }
  return result.stdout;
}

export function isAncestorRevision(
  rootDir: string,
  baseRevision: string,
  targetRevision: string,
): boolean {
  const result = spawnSync(
    "git",
    ["merge-base", "--is-ancestor", baseRevision, targetRevision],
    { cwd: rootDir, encoding: "utf8" },
  );
  if (result.status === 0) return true;
  if (result.status === 1) return false;
  throw new Error("Unable to verify company OG marker ancestry");
}

type RevisionSources = {
  companies: string;
  descriptions: string;
  industries: string;
  sourceVersion: string;
};

export function loadCompanySourcesAtRevision(
  rootDir: string,
  revision: string,
): RevisionSources {
  if (!GIT_REVISION.test(revision)) throw new Error("Invalid Git revision");
  const contents = COMPANY_DATA_PATHS.map((file) =>
    runGit(rootDir, ["show", `${revision}:${file}`])
  );
  return {
    companies: contents[0],
    descriptions: contents[1],
    industries: contents[2],
    sourceVersion: computeCompanyOgSourceVersionFromSources(
      SOURCE_HASH_PATHS.map((path, index) => ({ path, content: contents[index] })),
    ),
  };
}

function documentsAtRevision(
  rootDir: string,
  revision: string,
): { documents: CompanyOgDocument[]; sourceVersion: string } {
  const sources = loadCompanySourcesAtRevision(rootDir, revision);
  return {
    documents: buildCompanyDocuments(
      sources.companies,
      sources.descriptions,
      sources.industries,
      null,
    ),
    sourceVersion: sources.sourceVersion,
  };
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

async function bodyToText(body: unknown): Promise<string> {
  if (!body) throw new Error("R2 marker body is missing");
  if (
    typeof body === "object" &&
    "transformToString" in body &&
    typeof body.transformToString === "function"
  ) {
    return await body.transformToString();
  }
  const chunks: Uint8Array[] = [];
  for await (const chunk of body as AsyncIterable<Uint8Array | string>) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function isMissingObjectError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const name = "name" in error ? String(error.name) : "";
  const status = "$metadata" in error
    ? (error as { $metadata?: { httpStatusCode?: number } }).$metadata
      ?.httpStatusCode
    : undefined;
  return name === "NoSuchKey" || name === "NotFound" || status === 404;
}

async function readMarkerObject(
  client: S3Client,
  bucket: string,
  key: string,
): Promise<unknown | null> {
  try {
    const response = await client.send(new GetObjectCommand({
      Bucket: bucket,
      Key: key,
    }));
    const text = await bodyToText(response.Body);
    try {
      return JSON.parse(text);
    } catch {
      // Return a value the strict schema parser rejects. This keeps malformed
      // state distinguishable from transport failure and lets only the
      // explicitly approved bootstrap path replace it.
      return { malformedMarker: true };
    }
  } catch (error) {
    if (isMissingObjectError(error)) return null;
    throw error;
  }
}

export async function readPublishedBaseline(
  client: S3Client,
  bucket: string,
  rendererVersion: string,
): Promise<{ marker: CompanyOgCompletionMarker | null; problem: string | null }> {
  const currentKey = companyOgCurrentCompletionKey(rendererVersion);
  const currentValue = await readMarkerObject(client, bucket, currentKey);
  if (currentValue === null) {
    return { marker: null, problem: "current completion marker is missing" };
  }
  const current = parseCompanyOgCompletionMarker(
    currentValue,
    rendererVersion,
  );
  if (!current) {
    return { marker: null, problem: "current completion marker is legacy or malformed" };
  }
  if (
    current.locales.length !== ALL_LOCALES.length ||
    !ALL_LOCALES.every((locale) => current.locales.includes(locale))
  ) {
    return { marker: null, problem: "current completion marker is not a full locale matrix" };
  }

  const immutableKey = companyOgCompletionKeyForVersion(
    rendererVersion,
    current.sourceVersion,
  );
  const immutableValue = await readMarkerObject(client, bucket, immutableKey);
  const immutable = parseCompanyOgCompletionMarker(
    immutableValue,
    rendererVersion,
  );
  if (!immutable || !sameCompanyOgCompletionCoverage(current, immutable)) {
    return {
      marker: null,
      problem: "current completion marker does not match its immutable marker",
    };
  }
  return { marker: current, problem: null };
}

export class PutAttemptBudget {
  used = 0;

  constructor(readonly limit: number) {
    if (!Number.isSafeInteger(limit) || limit < 1) {
      throw new Error("PUT-attempt budget must be a positive integer");
    }
  }

  consume(key: string): void {
    if (this.used >= this.limit) {
      throw new Error(
        `R2 PUT-attempt budget exhausted before ${key} (${this.used}/${this.limit})`,
      );
    }
    this.used += 1;
  }
}

export function assertWritePlanWithinBudget(
  plannedWrites: number,
  maxPlannedWrites: number,
  maxPutAttempts: number,
): void {
  if (plannedWrites > maxPlannedWrites) {
    throw new Error(
      `Planned ${plannedWrites} R2 writes, above limit ${maxPlannedWrites}; zero PUTs performed`,
    );
  }
  if (plannedWrites > maxPutAttempts) {
    throw new Error(
      `Plan requires ${plannedWrites} first attempts, above runtime limit ${maxPutAttempts}; zero PUTs performed`,
    );
  }
}

export function shouldPublishCompanyOgCompletion(input: {
  failureCount: number;
  isFullMatrix: boolean;
  baselineSourceVersion: string | null;
  targetSourceVersion: string;
}): boolean {
  return input.failureCount === 0 &&
    input.isFullMatrix &&
    input.baselineSourceVersion !== input.targetSourceVersion;
}

async function budgetedPut(
  client: S3Client,
  budget: PutAttemptBudget,
  command: PutObjectCommand,
): Promise<void> {
  budget.consume(String(command.input.Key ?? "unknown"));
  await client.send(command);
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
  budget: PutAttemptBudget,
): Promise<void> {
  const response = await renderSiteOgCard();
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!isPng(bytes)) {
    throw new Error("Site OG renderer did not produce PNG bytes");
  }

  await budgetedPut(client, budget, new PutObjectCommand({
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
  budget = new PutAttemptBudget(3),
): Promise<"existing" | "uploaded"> {
  const existing = await listExistingKeys(client, bucket, "og/site/");
  if (existing.has(SITE_OG_KEY)) return "existing";

  await withRetry(() => renderAndUploadSiteOg(client, bucket, budget));
  return "uploaded";
}

async function renderAndUpload(
  client: S3Client,
  bucket: string,
  rendererVersion: string,
  task: CompanyOgRenderTask,
  budget: PutAttemptBudget,
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
  await budgetedPut(client, budget, new PutObjectCommand({
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

export async function publishCompanyOgCompletionMarkers(input: {
  client: S3Client;
  bucket: string;
  budget: PutAttemptBudget;
  marker: CompanyOgCompletionMarker;
  reuseImmutable: boolean;
}): Promise<{ completionMarker: string; currentMarker: string }> {
  const immutableKey = companyOgCompletionKeyForVersion(
    input.marker.rendererVersion,
    input.marker.sourceVersion,
  );
  if (!input.reuseImmutable) {
    await withRetry(() => budgetedPut(
      input.client,
      input.budget,
      new PutObjectCommand({
        Bucket: input.bucket,
        Key: immutableKey,
        Body: JSON.stringify(input.marker),
        ContentType: MARKER_CONTENT_TYPE,
        CacheControl: CACHE_CONTROL,
      }),
    ));
  }

  const currentKey = companyOgCurrentCompletionKey(
    input.marker.rendererVersion,
  );
  await withRetry(() => budgetedPut(
    input.client,
    input.budget,
    new PutObjectCommand({
      Bucket: input.bucket,
      Key: currentKey,
      Body: JSON.stringify(input.marker),
      ContentType: MARKER_CONTENT_TYPE,
      CacheControl: CURRENT_MARKER_CACHE_CONTROL,
    }),
  ));
  return { completionMarker: immutableKey, currentMarker: currentKey };
}

export async function main() {
  const options = parseOptions(process.argv.slice(2));
  if (!options.yes) throw new Error(`R2 writes require --yes.\n\n${usage()}`);

  const rootDir = process.cwd();
  const checkedOutRevision = runGit(rootDir, ["rev-parse", "HEAD"]).trim();
  if (!GIT_REVISION.test(checkedOutRevision)) {
    throw new Error("Unable to resolve the checked-out Git revision");
  }
  const targetRevision = options.targetRevision ?? checkedOutRevision;
  if (checkedOutRevision !== targetRevision) {
    throw new Error(
      `Checked-out revision ${checkedOutRevision} does not match target ${targetRevision}`,
    );
  }
  const rendererVersion = options.rendererVersion ??
    computeCompanyOgRendererVersion(rootDir);
  const target = documentsAtRevision(rootDir, targetRevision);
  const sourceVersion = target.sourceVersion;
  const prefix = `og/company/${rendererVersion}/`;
  const { client, bucket } = createR2Client();
  const baselineResult = await readPublishedBaseline(
    client,
    bucket,
    rendererVersion,
  ).catch((error) => {
    throw new PrewarmExternalError("r2", "read_company_og_baseline", error);
  });
  if (baselineResult.problem && !options.fullRebuild) {
    throw new Error(
      `${baselineResult.problem}; an explicitly approved full rebuild is required`,
    );
  }
  const baseline = baselineResult.marker;
  let baseDocuments: CompanyOgDocument[] = [];
  let lineageBaseRevision: string | null = null;
  if (!options.fullRebuild) {
    if (!baseline) throw new Error("Company OG baseline is unavailable");
    if (!isAncestorRevision(rootDir, baseline.revision, targetRevision)) {
      throw new Error(
        `Published revision ${baseline.revision} is not an ancestor of ${targetRevision}`,
      );
    }
    if (
      baseline.baseRevision !== null &&
      !isAncestorRevision(rootDir, baseline.baseRevision, baseline.revision)
    ) {
      throw new Error(
        "Published marker base revision is not an ancestor of its revision",
      );
    }
    const base = documentsAtRevision(rootDir, baseline.revision);
    if (base.sourceVersion !== baseline.sourceVersion) {
      throw new Error(
        "Published marker source version does not match its recorded Git revision",
      );
    }
    if (
      base.documents.length !== baseline.companies ||
      baseline.expected !== base.documents.length * ALL_LOCALES.length
    ) {
      throw new Error(
        "Published marker company count does not match its recorded Git revision",
      );
    }
    baseDocuments = base.documents;
    lineageBaseRevision = baseline.revision;
  }

  const [existingKeys, existingSiteKeys] = await Promise.all([
    listExistingKeys(client, bucket, prefix),
    listExistingKeys(client, bucket, "og/site/"),
  ]).catch((error) => {
    throw new PrewarmExternalError("r2", "list_company_og_cache", error);
  });
  const plan = planCompanyOgPrewarm({
    targetDocuments: target.documents,
    baseDocuments,
    existingKeys,
    rendererVersion,
    locales: options.locales,
    fullRebuild: options.fullRebuild,
    maxCompanies: options.maxCompanies,
  });
  const tasks = plan.tasks;
  const siteNeedsUpload = !existingSiteKeys.has(SITE_OG_KEY);
  const isFullMatrix = options.maxCompanies === null &&
    options.locales.length === ALL_LOCALES.length &&
    ALL_LOCALES.every((locale) => options.locales.includes(locale));
  const needsPublication = shouldPublishCompanyOgCompletion({
    failureCount: 0,
    isFullMatrix,
    baselineSourceVersion: baseline?.sourceVersion ?? null,
    targetSourceVersion: sourceVersion,
  });

  let reusableImmutableMarker: CompanyOgCompletionMarker | null = null;
  if (needsPublication) {
    const immutableKey = companyOgCompletionKeyForVersion(
      rendererVersion,
      sourceVersion,
    );
    const immutableValue = await readMarkerObject(client, bucket, immutableKey)
      .catch((error) => {
        throw new PrewarmExternalError(
          "r2",
          "read_company_og_target_marker",
          error,
        );
      });
    if (immutableValue !== null) {
      reusableImmutableMarker = parseCompanyOgCompletionMarker(
        immutableValue,
        rendererVersion,
      );
      const reusable = reusableImmutableMarker;
      const matchesCoverage = reusable?.sourceVersion === sourceVersion &&
        reusable.companies === target.documents.length &&
        reusable.expected === target.documents.length * ALL_LOCALES.length &&
        reusable.locales.length === ALL_LOCALES.length &&
        ALL_LOCALES.every((locale) => reusable.locales.includes(locale));
      let reusableSourceMatches = false;
      if (matchesCoverage && reusable) {
        try {
          if (isAncestorRevision(rootDir, reusable.revision, targetRevision)) {
            const immutableSource = documentsAtRevision(
              rootDir,
              reusable.revision,
            );
            reusableSourceMatches =
              immutableSource.sourceVersion === reusable.sourceVersion &&
              immutableSource.documents.length === reusable.companies;
          }
        } catch (error) {
          if (!options.fullRebuild) throw error;
        }
      }
      if (!matchesCoverage || !reusableSourceMatches) {
        if (!options.fullRebuild) {
          throw new Error(
            "Target immutable marker exists but does not match the revision plan",
          );
        }
        reusableImmutableMarker = null;
      }
    }
  }

  const markerWrites = needsPublication
    ? (reusableImmutableMarker ? 1 : 2)
    : 0;
  const plannedWrites = tasks.length + Number(siteNeedsUpload) + markerWrites;
  if (
    plannedWrites > options.maxPlannedWrites ||
    plannedWrites > options.maxPutAttempts
  ) {
    console.error(JSON.stringify({
      event: "company_og_prewarm_plan_rejected",
      rendererVersion,
      sourceVersion,
      targetRevision,
      baseRevision: lineageBaseRevision,
      plannedWrites,
      maxPlannedWrites: options.maxPlannedWrites,
      maxPutAttempts: options.maxPutAttempts,
      companyWrites: tasks.length,
      siteWrites: Number(siteNeedsUpload),
      markerWrites,
    }));
  }
  assertWritePlanWithinBudget(
    plannedWrites,
    options.maxPlannedWrites,
    options.maxPutAttempts,
  );
  const putBudget = new PutAttemptBudget(options.maxPutAttempts);

  console.log(JSON.stringify({
    event: "company_og_prewarm_started",
    rendererVersion,
    sourceVersion,
    targetRevision,
    baseRevision: lineageBaseRevision,
    companies: target.documents.length,
    locales: options.locales,
    changedCompanies: plan.changedSlugs.length,
    removedCompanies: plan.removedSlugs.length,
    missingKeys: plan.missingKeys,
    pending: tasks.length,
    plannedWrites,
    maxPlannedWrites: options.maxPlannedWrites,
    maxPutAttempts: options.maxPutAttempts,
    concurrency: options.concurrency,
    fullRebuild: options.fullRebuild,
    siteOg: siteNeedsUpload ? "pending" : "existing",
    markerWrites,
  }));

  let siteOg: "existing" | "uploaded" = "existing";
  if (siteNeedsUpload) {
    await withRetry(() => renderAndUploadSiteOg(
      client,
      bucket,
      putBudget,
    )).catch((error) => {
      throw new PrewarmExternalError("r2", "prewarm_site_og", error);
    });
    siteOg = "uploaded";
  }

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
          putBudget,
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
  if (shouldPublishCompanyOgCompletion({
    failureCount: failures.length,
    isFullMatrix,
    baselineSourceVersion: baseline?.sourceVersion ?? null,
    targetSourceVersion: sourceVersion,
  })) {
    const key = companyOgCompletionKeyForVersion(
      rendererVersion,
      sourceVersion,
    );
    let markerKey = key;
    try {
      const marker: CompanyOgCompletionMarker = {
        schemaVersion: COMPANY_OG_COMPLETION_SCHEMA_VERSION,
        complete: true,
        rendererVersion,
        sourceVersion,
        revision: targetRevision,
        baseRevision: lineageBaseRevision,
        companies: target.documents.length,
        locales: [...ALL_LOCALES],
        expected: target.documents.length * ALL_LOCALES.length,
        completedAt: new Date().toISOString(),
      };
      const published = await publishCompanyOgCompletionMarkers({
        client,
        bucket,
        budget: putBudget,
        marker,
        reuseImmutable: reusableImmutableMarker !== null,
      });
      completionMarker = published.completionMarker;
      currentMarker = published.currentMarker;
    } catch (error) {
      markerKey = completionMarker === null
        ? key
        : companyOgCurrentCompletionKey(rendererVersion);
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
    targetRevision,
    baseRevision: lineageBaseRevision,
    companies: target.documents.length,
    expected: target.documents.length * options.locales.length,
    changedCompanies: plan.changedSlugs.length,
    removedCompanies: plan.removedSlugs.length,
    missingKeys: plan.missingKeys,
    uploaded,
    failed: failures.length,
    plannedWrites,
    putAttempts: putBudget.used,
    maxPutAttempts: putBudget.limit,
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
