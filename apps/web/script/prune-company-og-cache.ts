/**
 * Prune stale company Open Graph PNG renderer-version namespaces from R2.
 *
 * The company OG cache stores objects under:
 *
 *   og/company/<renderer-version>/<locale>/<slug>.png
 *
 * Renderer-versioned keys make deploys safe, but old namespaces must be
 * pruned so the bucket does not grow forever. This script is dry-run by
 * default; pass `--yes` to delete.
 *
 * Run with:
 *   pnpm --filter @jobseek/web og:prune -- --retain-versions 8 --min-age-days 60
 */

import {
  DeleteObjectsCommand,
  ListObjectsV2Command,
  S3Client,
  type _Object,
} from "@aws-sdk/client-s3";

type Options = {
  prefix: string;
  retainVersions: number;
  minAgeDays: number;
  maxDelete: number;
  yes: boolean;
};

type R2Config = {
  endpoint: string;
  accessKeyId: string;
  secretAccessKey: string;
  bucket: string;
};

type VersionGroup = {
  version: string;
  objects: _Object[];
  latestModified: Date;
  bytes: number;
};

type ListObjectsPage = {
  contents: _Object[];
  nextContinuationToken?: string;
};

const DEFAULT_OPTIONS: Options = {
  prefix: "og/company/",
  retainVersions: 8,
  minAgeDays: 60,
  maxDelete: 20_000,
  yes: false,
};

function usage(): string {
  return [
    "Usage: pnpm --filter @jobseek/web og:prune -- [options]",
    "",
    "Options:",
    "  --yes                      Delete objects. Without this, dry-run only.",
    "  --retain-versions <n>      Keep the n newest renderer versions. Default: 8.",
    "  --min-age-days <n>         Only delete versions whose newest object is older than n days. Default: 60.",
    "  --max-delete <n>           Safety cap for objects deleted in one run. Default: 20000.",
    "  --prefix <prefix>          Object prefix. Default: og/company/.",
    "  --help                     Show this help.",
  ].join("\n");
}

function parseInteger(value: string | undefined, name: string): number {
  if (!value) throw new Error(`${name} requires a value`);
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative integer`);
  }
  return parsed;
}

function parseArgs(argv: string[]): Options {
  const options = { ...DEFAULT_OPTIONS };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    switch (arg) {
      case "--":
        break;
      case "--help":
      case "-h":
        console.log(usage());
        process.exit(0);
      case "--yes":
        options.yes = true;
        break;
      case "--retain-versions":
        options.retainVersions = parseInteger(argv[++i], arg);
        break;
      case "--min-age-days":
        options.minAgeDays = parseInteger(argv[++i], arg);
        break;
      case "--max-delete":
        options.maxDelete = parseInteger(argv[++i], arg);
        break;
      case "--prefix": {
        const prefix = argv[++i];
        if (!prefix) throw new Error("--prefix requires a value");
        options.prefix = prefix.endsWith("/") ? prefix : `${prefix}/`;
        break;
      }
      default:
        throw new Error(`Unknown argument: ${arg}\n\n${usage()}`);
    }
  }

  return options;
}

function getR2Config(): R2Config {
  const endpoint = process.env.R2_ENDPOINT_URL;
  const accessKeyId = process.env.R2_ACCESS_KEY_ID;
  const secretAccessKey = process.env.R2_SECRET_ACCESS_KEY;
  const bucket = process.env.R2_BUCKET;

  const missing = [
    ["R2_ENDPOINT_URL", endpoint],
    ["R2_ACCESS_KEY_ID", accessKeyId],
    ["R2_SECRET_ACCESS_KEY", secretAccessKey],
    ["R2_BUCKET", bucket],
  ]
    .filter(([, value]) => !value)
    .map(([name]) => name);

  if (missing.length > 0) {
    throw new Error(`Missing required env var(s): ${missing.join(", ")}`);
  }

  return {
    endpoint: endpoint!,
    accessKeyId: accessKeyId!,
    secretAccessKey: secretAccessKey!,
    bucket: bucket!,
  };
}

function getClient(config: R2Config): S3Client {
  return new S3Client({
    endpoint: config.endpoint,
    region: "auto",
    forcePathStyle: true,
    credentials: {
      accessKeyId: config.accessKeyId,
      secretAccessKey: config.secretAccessKey,
    },
  });
}

async function listObjects(
  client: S3Client,
  bucket: string,
  prefix: string,
): Promise<_Object[]> {
  const objects: _Object[] = [];
  let continuationToken: string | undefined;

  do {
    const page = await listObjectsPage(client, bucket, prefix, continuationToken);
    for (const object of page.contents) {
      objects.push({
        ...object,
        Key: object.Key ? decodeObjectKey(object.Key) : object.Key,
      });
    }
    continuationToken = page.nextContinuationToken;
  } while (continuationToken);

  return objects;
}

async function listObjectsPage(
  client: S3Client,
  bucket: string,
  prefix: string,
  continuationToken?: string,
): Promise<ListObjectsPage> {
  try {
    const response = await client.send(new ListObjectsV2Command({
      Bucket: bucket,
      Prefix: prefix,
      ContinuationToken: continuationToken,
      EncodingType: "url",
    }));
    return {
      contents: response.Contents ?? [],
      nextContinuationToken: response.NextContinuationToken,
    };
  } catch (error) {
    const response = (error as {
      $metadata?: { httpStatusCode?: number };
      $response?: { body?: unknown };
    }).$response;
    const status = (error as { $metadata?: { httpStatusCode?: number } }).$metadata
      ?.httpStatusCode;
    if (status !== 200 || !response?.body) throw error;

    // Some R2/S3-compatible list responses can trip the AWS SDK XML
    // deserializer even though the HTTP response is a valid 200 XML
    // ListBucketResult. Parse this small response shape ourselves so the
    // pruning workflow remains operational.
    return parseListObjectsXml(await bodyToString(response.body));
  }
}

function decodeObjectKey(key: string): string {
  try {
    return decodeURIComponent(key);
  } catch {
    return key;
  }
}

async function bodyToString(body: unknown): Promise<string> {
  const chunks: Uint8Array[] = [];
  for await (const chunk of body as AsyncIterable<Uint8Array>) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

function parseListObjectsXml(xml: string): ListObjectsPage {
  const contents: _Object[] = [];
  const contentRe = /<Contents>([\s\S]*?)<\/Contents>/gu;
  for (const match of xml.matchAll(contentRe)) {
    const block = match[1] ?? "";
    const key = tagValue(block, "Key");
    if (!key) continue;
    const lastModified = tagValue(block, "LastModified");
    const size = tagValue(block, "Size");
    contents.push({
      Key: key,
      LastModified: lastModified ? new Date(lastModified) : undefined,
      Size: size ? Number.parseInt(size, 10) : undefined,
    });
  }

  return {
    contents,
    nextContinuationToken: tagValue(xml, "NextContinuationToken") ?? undefined,
  };
}

function tagValue(xml: string, tag: string): string | null {
  const match = new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`, "u").exec(xml);
  return match ? decodeXmlEntities(match[1] ?? "") : null;
}

function decodeXmlEntities(value: string): string {
  return value
    .replace(/&#x([0-9a-f]+);/giu, (_, hex: string) =>
      String.fromCodePoint(Number.parseInt(hex, 16)),
    )
    .replace(/&#([0-9]+);/gu, (_, decimal: string) =>
      String.fromCodePoint(Number.parseInt(decimal, 10)),
    )
    .replace(/&quot;/gu, "\"")
    .replace(/&apos;/gu, "'")
    .replace(/&gt;/gu, ">")
    .replace(/&lt;/gu, "<")
    .replace(/&amp;/gu, "&");
}

function versionFromKey(key: string | undefined, prefix: string): string | null {
  if (!key?.startsWith(prefix)) return null;
  const rest = key.slice(prefix.length);
  const parts = rest.split("/");
  if (parts.length < 3) return null;
  const [version, locale, file] = parts;
  if (!version || !locale || !file?.endsWith(".png")) return null;
  return version;
}

function groupByVersion(objects: _Object[], prefix: string): VersionGroup[] {
  const map = new Map<string, VersionGroup>();

  for (const object of objects) {
    const version = versionFromKey(object.Key, prefix);
    if (!version) continue;

    const modified = object.LastModified ?? new Date(0);
    const bytes = object.Size ?? 0;
    const existing = map.get(version);
    if (existing) {
      existing.objects.push(object);
      existing.bytes += bytes;
      if (modified > existing.latestModified) existing.latestModified = modified;
    } else {
      map.set(version, {
        version,
        objects: [object],
        latestModified: modified,
        bytes,
      });
    }
  }

  return [...map.values()].sort(
    (a, b) => b.latestModified.getTime() - a.latestModified.getTime(),
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  for (const unit of units) {
    if (value < 1024) return `${value.toFixed(1)} ${unit}`;
    value /= 1024;
  }
  return `${value.toFixed(1)} PB`;
}

function summarizeVersion(group: VersionGroup): string {
  return [
    group.version,
    `${group.objects.length} object(s)`,
    formatBytes(group.bytes),
    `latest=${group.latestModified.toISOString()}`,
  ].join(" | ");
}

function chunk<T>(items: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
}

async function deleteObjects(
  client: S3Client,
  bucket: string,
  objects: _Object[],
): Promise<void> {
  for (const batch of chunk(objects, 1000)) {
    const keys = batch
      .map((object) => object.Key)
      .filter((key): key is string => typeof key === "string");
    if (keys.length === 0) continue;

    await client.send(new DeleteObjectsCommand({
      Bucket: bucket,
      Delete: {
        Objects: keys.map((Key) => ({ Key })),
        Quiet: true,
      },
    }));
    console.log(`[company-og-prune] deleted ${keys.length} object(s)`);
  }
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  const config = getR2Config();
  const client = getClient(config);

  const objects = await listObjects(client, config.bucket, options.prefix);
  const groups = groupByVersion(objects, options.prefix);
  const totalBytes = groups.reduce((sum, group) => sum + group.bytes, 0);
  const cutoff = Date.now() - options.minAgeDays * 24 * 60 * 60 * 1000;

  const retained = new Set(
    groups.slice(0, options.retainVersions).map((group) => group.version),
  );
  const deletableGroups = groups.filter(
    (group) =>
      !retained.has(group.version) &&
      group.latestModified.getTime() < cutoff,
  );
  const deletableObjects = deletableGroups.flatMap((group) => group.objects);
  const deletableBytes = deletableGroups.reduce((sum, group) => sum + group.bytes, 0);

  console.log(
    [
      `[company-og-prune] prefix=${options.prefix}`,
      `versions=${groups.length}`,
      `objects=${objects.length}`,
      `bytes=${formatBytes(totalBytes)}`,
      `retainVersions=${options.retainVersions}`,
      `minAgeDays=${options.minAgeDays}`,
      `mode=${options.yes ? "delete" : "dry-run"}`,
    ].join(" "),
  );

  if (groups.length > 0) {
    console.log("[company-og-prune] retained newest version(s):");
    for (const group of groups.slice(0, options.retainVersions)) {
      console.log(`  ${summarizeVersion(group)}`);
    }
  }

  if (deletableGroups.length === 0) {
    console.log("[company-og-prune] no stale renderer versions eligible for deletion");
    return;
  }

  console.log("[company-og-prune] stale version(s) eligible for deletion:");
  for (const group of deletableGroups) {
    console.log(`  ${summarizeVersion(group)}`);
  }
  console.log(
    `[company-og-prune] candidate delete: ${deletableObjects.length} object(s), ${formatBytes(deletableBytes)}`,
  );

  if (deletableObjects.length > options.maxDelete) {
    throw new Error(
      `Refusing to delete ${deletableObjects.length} objects; increase --max-delete above ${options.maxDelete}`,
    );
  }

  if (!options.yes) {
    console.log("[company-og-prune] dry-run only; pass --yes to delete");
    return;
  }

  await deleteObjects(client, config.bucket, deletableObjects);
  console.log("[company-og-prune] done");
}

main().catch((err) => {
  console.error("[company-og-prune] failed", err);
  process.exit(1);
});
