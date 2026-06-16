import "server-only";

import {
  GetObjectCommand,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";

const CONTENT_TYPE = "image/png";
const CACHE_CONTROL = "public, max-age=31536000, immutable";

let client: S3Client | null = null;

function getRendererVersion(): string {
  return (
    process.env.COMPANY_OG_RENDERER_VERSION ||
    process.env.VERCEL_GIT_COMMIT_SHA?.slice(0, 16) ||
    "local"
  );
}

export function shouldBypassCompanyOgCache(): boolean {
  return process.env.COMPANY_OG_CACHE_BYPASS === "1";
}

function segment(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 120) || "unknown";
}

export function companyOgCacheKey(locale: string, slug: string): string {
  return `og/company/${getRendererVersion()}/${segment(locale)}/${segment(slug)}.png`;
}

function getR2Config():
  | {
      endpoint: string;
      accessKeyId: string;
      secretAccessKey: string;
      bucket: string;
    }
  | null {
  const endpoint = process.env.R2_ENDPOINT_URL;
  const accessKeyId = process.env.R2_ACCESS_KEY_ID;
  const secretAccessKey = process.env.R2_SECRET_ACCESS_KEY;
  const bucket = process.env.R2_BUCKET;
  if (!endpoint || !accessKeyId || !secretAccessKey || !bucket) return null;
  return { endpoint, accessKeyId, secretAccessKey, bucket };
}

function getClient(config: NonNullable<ReturnType<typeof getR2Config>>): S3Client {
  if (client) return client;
  client = new S3Client({
    endpoint: config.endpoint,
    region: "auto",
    forcePathStyle: true,
    credentials: {
      accessKeyId: config.accessKeyId,
      secretAccessKey: config.secretAccessKey,
    },
  });
  return client;
}

async function bodyToBytes(body: unknown): Promise<Uint8Array | null> {
  if (!body) return null;
  if (
    typeof body === "object" &&
    body !== null &&
    "transformToByteArray" in body &&
    typeof body.transformToByteArray === "function"
  ) {
    return await body.transformToByteArray();
  }

  const chunks: Uint8Array[] = [];
  for await (const chunk of body as AsyncIterable<Uint8Array>) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

function isMissingObjectError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const name = "name" in error ? String(error.name) : "";
  const code = "$metadata" in error
    ? (error as { $metadata?: { httpStatusCode?: number } }).$metadata?.httpStatusCode
    : undefined;
  return name === "NoSuchKey" || name === "NotFound" || code === 404;
}

export async function readCompanyOgCache(key: string): Promise<Uint8Array | null> {
  const config = getR2Config();
  if (!config) return null;

  try {
    const response = await getClient(config).send(new GetObjectCommand({
      Bucket: config.bucket,
      Key: key,
    }));
    return bodyToBytes(response.Body);
  } catch (error) {
    if (isMissingObjectError(error)) return null;
    console.warn("[company-og-cache] read failed", error);
    return null;
  }
}

export async function writeCompanyOgCache(
  key: string,
  body: Uint8Array,
): Promise<void> {
  const config = getR2Config();
  if (!config) return;

  try {
    await getClient(config).send(new PutObjectCommand({
      Bucket: config.bucket,
      Key: key,
      Body: body,
      ContentType: CONTENT_TYPE,
      CacheControl: CACHE_CONTROL,
    }));
  } catch (error) {
    console.warn("[company-og-cache] write failed", error);
  }
}
