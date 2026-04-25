import { S3Client, GetObjectCommand, PutObjectCommand } from "@aws-sdk/client-s3";

let r2Client: S3Client | null = null;

function getR2Client(): S3Client {
  if (r2Client) return r2Client;

  const endpoint = process.env.R2_ENDPOINT_URL;
  const accessKeyId = process.env.R2_ACCESS_KEY_ID;
  const secretAccessKey = process.env.R2_SECRET_ACCESS_KEY;

  if (!endpoint || !accessKeyId || !secretAccessKey) {
    throw new Error("R2 credentials are not fully configured");
  }

  r2Client = new S3Client({
    region: "auto",
    endpoint,
    credentials: { accessKeyId, secretAccessKey },
  });

  return r2Client;
}

function getR2Bucket(): string {
  const bucket = process.env.R2_BUCKET;
  if (!bucket) throw new Error("R2_BUCKET is not set");
  return bucket;
}

export async function getResumeFromR2(key: string): Promise<string | null> {
  try {
    const response = await getR2Client().send(
      new GetObjectCommand({
        Bucket: getR2Bucket(),
        Key: key,
      }),
    );

    if (!response.Body) return null;

    const reader = response.Body as any;
    const chunks: Uint8Array[] = [];
    
    if (reader && typeof reader[Symbol.asyncIterator] === "function") {
      for await (const chunk of reader) {
        chunks.push(chunk instanceof Uint8Array ? chunk : new Uint8Array(chunk));
      }
    }

    const buffer = Buffer.concat(chunks);
    return buffer.toString("utf-8");
  } catch {
    return null;
  }
}

export async function saveResumeToR2(
  key: string,
  content: string,
  contentType: string = "text/plain",
): Promise<boolean> {
  try {
    await getR2Client().send(
      new PutObjectCommand({
        Bucket: getR2Bucket(),
        Key: key,
        Body: content,
        ContentType: contentType,
      }),
    );
    return true;
  } catch (err) {
    console.error("Failed to save resume to R2:", err);
    return false;
  }
}

export function buildResumeKey(userId: string, queueId: string, type: "original" | "customized"): string {
  const timestamp = Date.now();
  return `resumes/${userId}/${queueId}-${type}-${timestamp}.tex`;
}
