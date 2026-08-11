/**
 * Pre-render company Open Graph cards outside Vercel Functions.
 *
 * The script pages through the production Typesense company collection once,
 * renders the exact same Satori card used by the Next.js fallback route, and
 * uploads only missing objects in the current renderer-version namespace.
 */
import {
  ListObjectsV2Command,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { setTimeout as delay } from "node:timers/promises";
import { Client as TypesenseClient } from "typesense";
import {
  logExternalError,
  safeExternalError,
  type ExternalService,
  type SafeExternalError,
} from "@/lib/safe-external-error";
import { mapTypesenseCompanyHitToDetail } from "@/lib/services/company-detail-lookup";
import { renderCompanyOgCard } from "@/lib/og/company-og-card";
import { companyOgCacheKeyForVersion } from "@/lib/og/company-og-key";
import { computeCompanyOgRendererVersion } from "@/lib/og/company-og-renderer-version";

const ALL_LOCALES = ["en", "de", "fr", "it"] as const;
const CONTENT_TYPE = "image/png";
const CACHE_CONTROL = "public, max-age=31536000, immutable";
const TYPESENSE_PAGE_SIZE = 250;
export const TYPESENSE_BATCH_TIMEOUT_SECONDS = 30;

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

function createPrewarmTypesenseClient(): TypesenseClient {
  const host = requiredEnvironment("TYPESENSE_HOST");
  const port = Number.parseInt(requiredEnvironment("TYPESENSE_PORT"), 10);
  const protocol = requiredEnvironment("TYPESENSE_PROTOCOL");
  const apiKey = requiredEnvironment("TYPESENSE_SEARCH_KEY");
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) {
    throw new Error("TYPESENSE_PORT must be a valid TCP port");
  }

  // Batch jobs traverse the public Cloudflare tunnel from a GitHub runner and
  // need a wider timeout than latency-sensitive production requests. Keep
  // this isolated from getSearchClient(), whose five-second budget is correct
  // for interactive web traffic.
  return new TypesenseClient({
    nodes: [{ host, port, protocol }],
    apiKey,
    connectionTimeoutSeconds: TYPESENSE_BATCH_TIMEOUT_SECONDS,
    numRetries: 1,
    retryIntervalSeconds: 1,
  });
}

async function listCompanies(
  client: TypesenseClient,
  maxCompanies: number | null,
) {
  const companies: Record<string, unknown>[] = [];
  let page = 1;

  while (true) {
    const result = await withRetry(
      () => client.collections<Record<string, unknown>>("company")
        .documents()
        .search({
          q: "*",
          per_page: TYPESENSE_PAGE_SIZE,
          page,
        }),
      1_000,
    );
    const hits = result.hits ?? [];
    for (const hit of hits) {
      companies.push(hit.document);
      if (maxCompanies && companies.length >= maxCompanies) return companies;
    }
    if (hits.length < TYPESENSE_PAGE_SIZE || companies.length >= result.found) break;
    page += 1;
  }

  return companies;
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

  // Validate Typesense configuration before any R2 writes begin.
  const typesenseClient = createPrewarmTypesenseClient();

  const rendererVersion = options.rendererVersion ??
    computeCompanyOgRendererVersion(process.cwd());
  const prefix = `og/company/${rendererVersion}/`;
  const { client, bucket } = createR2Client();
  const [companies, existingKeys] = await Promise.all([
    listCompanies(typesenseClient, options.maxCompanies).catch((error) => {
      throw new PrewarmExternalError(
        "typesense",
        "list_company_og_source",
        error,
      );
    }),
    options.force
      ? Promise.resolve(new Set<string>())
      : listExistingKeys(client, bucket, prefix).catch((error) => {
          throw new PrewarmExternalError(
            "r2",
            "list_company_og_cache",
            error,
          );
        }),
  ]);

  const tasks: RenderTask[] = [];
  let skipped = 0;
  for (const company of companies) {
    const slug = typeof company.slug === "string" ? company.slug : "";
    if (!slug) throw new Error(`Typesense company document ${String(company.id)} has no slug`);
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
    companies: companies.length,
    locales: options.locales,
    existing: skipped,
    pending: tasks.length,
    concurrency: options.concurrency,
    force: options.force,
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

  console.log(JSON.stringify({
    event: "company_og_prewarm_completed",
    rendererVersion,
    companies: companies.length,
    expected: companies.length * options.locales.length,
    skipped,
    uploaded,
    failed: failures.length,
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
