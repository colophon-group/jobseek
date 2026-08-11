import "server-only";

import https from "node:https";
import type { S3Client } from "@aws-sdk/client-s3";
import { logExternalError } from "@/lib/safe-external-error";

export { companyOgCacheKey } from "@/lib/og/company-og-key";

const CONTENT_TYPE = "image/png";
const CACHE_CONTROL = "public, max-age=31536000, immutable";

let client: S3Client | null = null;

export function shouldBypassCompanyOgCache(): boolean {
  return process.env.COMPANY_OG_CACHE_BYPASS === "1";
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

async function getClient(
  config: NonNullable<ReturnType<typeof getR2Config>>,
): Promise<S3Client> {
  if (client) return client;
  const { S3Client } = await import("@aws-sdk/client-s3");
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

function getPublicObjectUrl(key: string): string | null {
  const domain = process.env.R2_DOMAIN_URL;
  if (!domain) return null;

  try {
    const base = new URL(domain.endsWith("/") ? domain : `${domain}/`);
    if (base.protocol !== "https:") return null;
    return new URL(key.split("/").map(encodeURIComponent).join("/"), base).toString();
  } catch {
    return null;
  }
}

async function readPublicObject(
  url: string,
): Promise<{ status: number; bytes: Uint8Array | null }> {
  return new Promise((resolve, reject) => {
    const request = https.request(url, { method: "GET" }, (response) => {
      const status = response.statusCode ?? 0;
      if (status < 200 || status >= 300) {
        response.resume();
        resolve({ status, bytes: null });
        return;
      }

      void bodyToBytes(response)
        .then((bytes) => resolve({ status, bytes }))
        .catch(reject);
    });
    request.setTimeout(3000, () => {
      request.destroy(new Error("R2 public cache probe timed out"));
    });
    request.on("error", reject);
    request.end();
  });
}

async function bodyToBytes(body: unknown): Promise<Uint8Array | null> {
  if (!body) return null;
  if (
    typeof body === "object" &&
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

async function readCompanyOgCacheSigned(key: string): Promise<Uint8Array | null> {
  const config = getR2Config();
  if (!config) return null;

  try {
    const { GetObjectCommand } = await import("@aws-sdk/client-s3");
    const response = await (await getClient(config)).send(new GetObjectCommand({
      Bucket: config.bucket,
      Key: key,
    }));
    return bodyToBytes(response.Body);
  } catch (error) {
    if (isMissingObjectError(error)) return null;
    logExternalError("warn", { service: "r2", operation: "read_company_og" }, error);
    return null;
  }
}

export async function readCompanyOgCache(
  key: string,
): Promise<Uint8Array | null> {
  const publicUrl = getPublicObjectUrl(key);
  if (publicUrl) {
    try {
      const result = await readPublicObject(publicUrl);
      if (result.bytes) return result.bytes;
      if (result.status === 404) return null;
    } catch (error) {
      logExternalError(
        "warn",
        { service: "r2", operation: "probe_public_company_og" },
        error,
      );
    }
  }

  return readCompanyOgCacheSigned(key);
}

export async function writeCompanyOgCache(
  key: string,
  body: Uint8Array,
): Promise<void> {
  const config = getR2Config();
  if (!config) return;

  try {
    const { PutObjectCommand } = await import("@aws-sdk/client-s3");
    await (await getClient(config)).send(new PutObjectCommand({
      Bucket: config.bucket,
      Key: key,
      Body: body,
      ContentType: CONTENT_TYPE,
      CacheControl: CACHE_CONTROL,
    }));
  } catch (error) {
    logExternalError("warn", { service: "r2", operation: "write_company_og" }, error);
  }
}
