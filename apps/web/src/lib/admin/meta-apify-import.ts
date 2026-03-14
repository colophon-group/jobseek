import "server-only";

import { createHash } from "node:crypto";
import { S3Client, GetObjectCommand, PutObjectCommand } from "@aws-sdk/client-s3";
import { eq, inArray, sql } from "drizzle-orm";
import { db } from "@/db";
import { company, jobBoard, jobPosting } from "@/db/schema";

const APIFY_BASE_URL = "https://api.apify.com/v2";
const META_BOARD_SLUG = "meta-careers";

export class MetaApifyImportError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "MetaApifyImportError";
  }
}

export type MetaApifyJob = {
  url: string;
  title: string | null;
  description: string | null;
  locations: string[] | null;
  employmentType: string | null;
  jobLocationType: string | null;
  datePosted: string | null;
  language: string | null;
  extras: Record<string, unknown> | null;
  metadata: Record<string, unknown> | null;
  localizations: Record<string, unknown> | null;
};

type BoardConfig = {
  boardId: string;
  boardSlug: string;
  companyId: string;
  actorId: string;
};

type LatestApifyDataset = {
  actorId: string;
  runId: string;
  datasetId: string;
  items: unknown[];
};

type ExistingPosting = {
  id: string;
  descriptionR2Hash: bigint | null;
};

type PostingMutation = {
  companyId: string;
  boardId: string;
  sourceUrl: string;
  employmentType: string | null;
  titles: string[];
  locales: string[];
  locationIds: number[] | null;
  locationTypes: string[] | null;
  seenAt: Date;
};

type DescriptionPersistInput = {
  postingId: string;
  currentHash: bigint | null;
  title: string | null;
  description: string | null;
  language: string | null;
  locations: string[] | null;
  localizations: Record<string, unknown> | null;
  extras: Record<string, unknown> | null;
  metadata: Record<string, unknown> | null;
  datePosted: string | null;
  employmentType: string | null;
  jobLocationType: string | null;
};

type DescriptionPersistResult =
  | { status: "skipped" }
  | { status: "unchanged"; hash: bigint }
  | { status: "uploaded"; hash: bigint };

type LocationResolution = {
  locationIds: number[] | null;
  locationTypes: string[] | null;
};

export type MetaApifyImportResult = {
  boardSlug: string;
  actorId: string;
  runId: string;
  datasetId: string;
  fetched: number;
  skippedMissingUrl: number;
  inserted: number;
  updated: number;
  r2Uploaded: number;
  r2Unchanged: number;
};

export type MetaApifyImportDeps = {
  now(): Date;
  getBoardConfig(): Promise<BoardConfig | null>;
  getLatestDataset(actorId: string): Promise<LatestApifyDataset>;
  getExistingPostings(urls: string[]): Promise<Map<string, ExistingPosting>>;
  insertPosting(input: PostingMutation): Promise<{ id: string }>;
  updatePosting(id: string, input: PostingMutation): Promise<void>;
  updatePostingHash(id: string, hash: bigint): Promise<void>;
  resolveLocations(
    locations: string[] | null,
    jobLocationType: string | null,
    language: string | null,
  ): Promise<LocationResolution>;
  persistDescription(input: DescriptionPersistInput): Promise<DescriptionPersistResult>;
};

export async function importLatestMetaApifyRun(
  deps: MetaApifyImportDeps = createDefaultDeps(),
): Promise<MetaApifyImportResult> {
  const board = await deps.getBoardConfig();
  if (!board) {
    throw new MetaApifyImportError(`Job board ${META_BOARD_SLUG} was not found`, 404);
  }

  const dataset = await deps.getLatestDataset(board.actorId);
  const { jobs, skippedMissingUrl } = mapApifyDatasetItems(dataset.items);
  const existing = await deps.getExistingPostings(jobs.map((job) => job.url));

  let inserted = 0;
  let updated = 0;
  let r2Uploaded = 0;
  let r2Unchanged = 0;

  for (const job of jobs) {
    const language = job.language ?? "en";
    const resolved = await deps.resolveLocations(
      job.locations,
      job.jobLocationType,
      language,
    );
    const mutation: PostingMutation = {
      companyId: board.companyId,
      boardId: board.boardId,
      sourceUrl: job.url,
      employmentType: normalizeEmploymentType(job.employmentType),
      titles: buildTitles(job.title, job.localizations),
      locales: buildLocales(language, job.localizations),
      locationIds: resolved.locationIds,
      locationTypes: resolved.locationTypes,
      seenAt: deps.now(),
    };

    const existingPosting = existing.get(job.url);
    let postingId: string;
    let currentHash: bigint | null = existingPosting?.descriptionR2Hash ?? null;

    if (existingPosting) {
      await deps.updatePosting(existingPosting.id, mutation);
      postingId = existingPosting.id;
      updated += 1;
    } else {
      const insertedPosting = await deps.insertPosting(mutation);
      postingId = insertedPosting.id;
      inserted += 1;
    }

    const persistResult = await deps.persistDescription({
      postingId,
      currentHash,
      title: job.title,
      description: job.description,
      language,
      locations: job.locations,
      localizations: job.localizations,
      extras: job.extras,
      metadata: job.metadata,
      datePosted: job.datePosted,
      employmentType: job.employmentType,
      jobLocationType: job.jobLocationType,
    });

    if (persistResult.status === "uploaded") {
      r2Uploaded += 1;
      if (persistResult.hash !== currentHash) {
        await deps.updatePostingHash(postingId, persistResult.hash);
        currentHash = persistResult.hash;
      }
    } else if (persistResult.status === "unchanged") {
      r2Unchanged += 1;
      if (currentHash == null || persistResult.hash !== currentHash) {
        await deps.updatePostingHash(postingId, persistResult.hash);
        currentHash = persistResult.hash;
      }
    }

    existing.set(job.url, { id: postingId, descriptionR2Hash: currentHash });
  }

  return {
    boardSlug: board.boardSlug,
    actorId: dataset.actorId,
    runId: dataset.runId,
    datasetId: dataset.datasetId,
    fetched: jobs.length,
    skippedMissingUrl,
    inserted,
    updated,
    r2Uploaded,
    r2Unchanged,
  };
}

export function mapApifyDatasetItems(items: unknown[]): {
  jobs: MetaApifyJob[];
  skippedMissingUrl: number;
} {
  const jobs = new Map<string, MetaApifyJob>();
  let skippedMissingUrl = 0;

  for (const raw of items) {
    if (!raw || typeof raw !== "object") continue;
    const item = raw as Record<string, unknown>;
    const url = asNonEmptyString(item.url);
    if (!url) {
      skippedMissingUrl += 1;
      continue;
    }

    const metadata: Record<string, unknown> = {};
    const teams = asStringArray(item.teams);
    const subTeams = asStringArray(item.subTeams);
    if (teams?.length) metadata.teams = teams;
    if (subTeams?.length) metadata.sub_teams = subTeams;

    const extras: Record<string, unknown> = {};
    const responsibilities = asNonEmptyString(item.responsibilities);
    const qualifications = asNonEmptyString(item.qualifications);
    if (responsibilities) extras.responsibilities = responsibilities;
    if (qualifications) extras.qualifications = qualifications;

    jobs.set(url, {
      url,
      title: asNonEmptyString(item.title),
      description: normalizeDescriptionHtml(asNonEmptyString(item.description)),
      locations: asStringArray(item.locations),
      employmentType: asNonEmptyString(item.employmentType),
      jobLocationType: asNonEmptyString(item.jobLocationType),
      datePosted: asNonEmptyString(item.datePosted),
      language: asNonEmptyString(item.language),
      extras: Object.keys(extras).length ? extras : null,
      metadata: Object.keys(metadata).length ? metadata : null,
      localizations: asRecord(item.localizations),
    });
  }

  return {
    jobs: [...jobs.values()],
    skippedMissingUrl,
  };
}

export function normalizeEmploymentType(raw: string | null): string | null {
  if (!raw) return null;
  const key = raw.trim().toLowerCase();
  if (!key) return null;

  const mapped = EMPLOYMENT_TYPE_MAP[key];
  return mapped ?? "full_time";
}

function normalizeJobLocationType(raw: string | null): string | null {
  if (!raw) return null;
  const key = raw.trim().toLowerCase();
  if (!key) return null;

  const mapped = JOB_LOCATION_TYPE_MAP[key];
  return mapped ?? "onsite";
}

function buildTitles(
  title: string | null,
  localizations: Record<string, unknown> | null,
): string[] {
  const titles: string[] = [];
  if (title) titles.push(title);
  if (localizations) {
    for (const value of Object.values(localizations)) {
      if (!value || typeof value !== "object") continue;
      const localizedTitle = asNonEmptyString((value as Record<string, unknown>).title);
      if (localizedTitle && !titles.includes(localizedTitle)) {
        titles.push(localizedTitle);
      }
    }
  }
  return titles;
}

function buildLocales(
  language: string | null,
  localizations: Record<string, unknown> | null,
): string[] {
  const locales = [language || "en"];
  if (localizations) {
    for (const locale of Object.keys(localizations)) {
      if (!locales.includes(locale)) locales.push(locale);
    }
  }
  return locales;
}

function createDefaultDeps(): MetaApifyImportDeps {
  return {
    now: () => new Date(),
    getBoardConfig,
    getLatestDataset,
    getExistingPostings,
    insertPosting,
    updatePosting,
    updatePostingHash,
    resolveLocations,
    persistDescription: persistDescriptionToR2,
  };
}

async function getBoardConfig(): Promise<BoardConfig | null> {
  const rows = await db
    .select({
      boardId: jobBoard.id,
      boardSlug: jobBoard.boardSlug,
      companyId: company.id,
      metadata: jobBoard.metadata,
    })
    .from(jobBoard)
    .innerJoin(company, eq(jobBoard.companyId, company.id))
    .where(eq(jobBoard.boardSlug, META_BOARD_SLUG))
    .limit(1);

  const row = rows[0];
  if (!row) return null;

  const metadata = asRecord(row.metadata);
  const actorId = asNonEmptyString(metadata?.actor_id);
  if (!actorId) {
    throw new MetaApifyImportError(
      `Board ${META_BOARD_SLUG} is missing metadata.actor_id`,
      500,
    );
  }

  return {
    boardId: row.boardId,
    boardSlug: row.boardSlug ?? META_BOARD_SLUG,
    companyId: row.companyId,
    actorId,
  };
}

async function getLatestDataset(actorId: string): Promise<LatestApifyDataset> {
  const token = process.env.APIFY_TOKEN;
  if (!token) {
    throw new MetaApifyImportError("APIFY_TOKEN is not set", 500);
  }

  const runsUrl = new URL(`${APIFY_BASE_URL}/acts/${actorId}/runs`);
  runsUrl.searchParams.set("status", "SUCCEEDED");
  runsUrl.searchParams.set("limit", "1");
  runsUrl.searchParams.set("desc", "1");

  const runsResponse = await fetch(runsUrl, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (!runsResponse.ok) {
    throw new MetaApifyImportError(
      `Apify runs request failed with ${runsResponse.status}`,
      502,
    );
  }

  const runsPayload = (await runsResponse.json()) as {
    data?: Array<Record<string, unknown>>;
  };
  const run = runsPayload.data?.[0];
  const runId = asNonEmptyString(run?.id);
  const datasetId = asNonEmptyString(run?.defaultDatasetId);

  if (!runId || !datasetId) {
    throw new MetaApifyImportError(
      `No successful Apify run found for actor ${actorId}`,
      502,
    );
  }

  const datasetUrl = new URL(`${APIFY_BASE_URL}/datasets/${datasetId}/items`);
  datasetUrl.searchParams.set("format", "json");
  datasetUrl.searchParams.set("clean", "true");

  const datasetResponse = await fetch(datasetUrl, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (!datasetResponse.ok) {
    throw new MetaApifyImportError(
      `Apify dataset request failed with ${datasetResponse.status}`,
      502,
    );
  }

  const items = await datasetResponse.json();
  if (!Array.isArray(items)) {
    throw new MetaApifyImportError("Apify dataset items response was not an array", 502);
  }

  return {
    actorId,
    runId,
    datasetId,
    items,
  };
}

async function getExistingPostings(urls: string[]): Promise<Map<string, ExistingPosting>> {
  if (urls.length === 0) return new Map();

  const rows = await db
    .select({
      id: jobPosting.id,
      sourceUrl: jobPosting.sourceUrl,
      descriptionR2Hash: jobPosting.descriptionR2Hash,
    })
    .from(jobPosting)
    .where(inArray(jobPosting.sourceUrl, urls));

  return new Map(
    rows.map((row) => [
      row.sourceUrl,
      {
        id: row.id,
        descriptionR2Hash: row.descriptionR2Hash,
      },
    ]),
  );
}

async function insertPosting(input: PostingMutation): Promise<{ id: string }> {
  const rows = await db
    .insert(jobPosting)
    .values({
      companyId: input.companyId,
      boardId: input.boardId,
      isActive: true,
      sourceUrl: input.sourceUrl,
      firstSeenAt: input.seenAt,
      lastSeenAt: input.seenAt,
      nextScrapeAt: null,
      employmentType: input.employmentType,
      titles: input.titles,
      locales: input.locales,
      locationIds: input.locationIds,
      locationTypes: input.locationTypes,
    })
    .returning({ id: jobPosting.id });

  const inserted = rows[0];
  if (!inserted) {
    throw new MetaApifyImportError(`Failed to insert posting ${input.sourceUrl}`, 500);
  }
  return inserted;
}

async function updatePosting(id: string, input: PostingMutation): Promise<void> {
  await db
    .update(jobPosting)
    .set({
      companyId: input.companyId,
      boardId: input.boardId,
      isActive: true,
      lastSeenAt: input.seenAt,
      nextScrapeAt: null,
      employmentType: input.employmentType,
      titles: input.titles,
      locales: input.locales,
      locationIds: input.locationIds,
      locationTypes: input.locationTypes,
    })
    .where(eq(jobPosting.id, id));
}

async function updatePostingHash(id: string, hash: bigint): Promise<void> {
  await db
    .update(jobPosting)
    .set({
      descriptionR2Hash: hash,
      toBeEnriched: true,
    })
    .where(eq(jobPosting.id, id));
}

async function resolveLocations(
  locations: string[] | null,
  jobLocationType: string | null,
  _language: string | null,
): Promise<LocationResolution> {
  const normalizedLocationType = normalizeJobLocationType(jobLocationType);
  const candidates = buildLocationCandidates(locations);
  if (candidates.length === 0) {
    return { locationIds: null, locationTypes: null };
  }

  const values = candidates.map((candidate) => sql`${candidate}`);
  const rows = await db.execute<{
    key: string;
    id: number;
    type: "macro" | "country" | "region" | "city";
  }>(sql`
    SELECT DISTINCT ON (lower(ln.name), l.id)
      lower(ln.name) AS key,
      l.id,
      l.type::text AS type
    FROM location_name ln
    JOIN location l ON l.id = ln.location_id
    WHERE lower(ln.name) IN (${sql.join(values, sql`, `)})
    ORDER BY lower(ln.name), l.id
  `);

  const seenIds = new Set<number>();
  const locationIds: number[] = [];
  const locationTypes: string[] = [];

  for (const row of rows) {
    if (seenIds.has(row.id)) continue;
    seenIds.add(row.id);
    locationIds.push(row.id);
    locationTypes.push(normalizedLocationType ?? row.type);
  }

  if (locationIds.length === 0) {
    return { locationIds: null, locationTypes: null };
  }

  return { locationIds, locationTypes };
}

async function persistDescriptionToR2(
  input: DescriptionPersistInput,
): Promise<DescriptionPersistResult> {
  if (!input.description) {
    return { status: "skipped" };
  }

  const locale = input.language || "en";
  const extras = buildR2Extras(input);
  const hash = computeR2Hash(input.description, extras);

  if (input.currentHash != null && input.currentHash === hash) {
    return { status: "unchanged", hash };
  }

  const latestKey = `job/${input.postingId}/${locale}/latest.html`;
  const historyKey = `job/${input.postingId}/${locale}/history.json`;

  const existingHtml = await getObjectText(latestKey);
  const existingHistory = parseHistory(await getObjectText(historyKey));
  const existingExtras = asRecord(existingHistory.current_extras) ?? {};

  const descriptionChanged = existingHtml !== null && existingHtml !== input.description;
  const extrasChanged = diffExtras(existingExtras, extras);
  const isFirstUpload = existingHtml === null;

  if (!isFirstUpload && !descriptionChanged && Object.keys(extrasChanged).length === 0) {
    return { status: "unchanged", hash };
  }

  if (isFirstUpload) {
    await putObject(historyKey, JSON.stringify({
      versions: [],
      current_extras: extras,
    }), "application/json");
  } else {
    const entry: Record<string, unknown> = {
      timestamp: new Date().toISOString(),
    };
    if (descriptionChanged && existingHtml !== null) {
      entry.diff = computeReverseDiff(input.description, existingHtml);
    }
    if (Object.keys(extrasChanged).length > 0) {
      entry.extras = extrasChanged;
    }
    await putObject(historyKey, JSON.stringify({
      versions: [entry, ...existingHistory.versions],
      current_extras: extras,
    }), "application/json");
  }

  if (isFirstUpload || descriptionChanged) {
    await putObject(latestKey, input.description, "text/html");
  }

  if (input.localizations) {
    for (const [localizationLocale, localizationValue] of Object.entries(input.localizations)) {
      if (localizationLocale === locale) continue;
      if (!localizationValue || typeof localizationValue !== "object") continue;
      const localizedDescription = asNonEmptyString(
        (localizationValue as Record<string, unknown>).description,
      );
      if (!localizedDescription) continue;
      await persistSecondaryDescription(input.postingId, localizationLocale, localizedDescription);
    }
  }

  return { status: "uploaded", hash };
}

async function persistSecondaryDescription(
  postingId: string,
  locale: string,
  html: string,
): Promise<void> {
  const latestKey = `job/${postingId}/${locale}/latest.html`;
  const historyKey = `job/${postingId}/${locale}/history.json`;
  const existingHtml = await getObjectText(latestKey);

  if (existingHtml === html) return;

  if (existingHtml === null) {
    await putObject(historyKey, JSON.stringify({ versions: [] }), "application/json");
  } else {
    const history = parseHistory(await getObjectText(historyKey));
    history.versions.unshift({
      timestamp: new Date().toISOString(),
      diff: computeReverseDiff(html, existingHtml),
    });
    await putObject(historyKey, JSON.stringify(history), "application/json");
  }

  await putObject(latestKey, html, "text/html");
}

function buildR2Extras(input: Omit<DescriptionPersistInput, "postingId" | "currentHash">): Record<string, unknown> {
  const merged: Record<string, unknown> = {};
  if (input.extras) Object.assign(merged, input.extras);
  if (input.title) merged.title = input.title;
  if (input.locations?.length) merged.locations = input.locations;
  if (input.metadata) merged.metadata = input.metadata;
  if (input.datePosted) merged.date_posted = input.datePosted;
  if (input.employmentType) merged.raw_employment_type = input.employmentType;
  if (input.jobLocationType) merged.raw_job_location_type = input.jobLocationType;
  return merged;
}

function computeR2Hash(description: string, extras: Record<string, unknown>): bigint {
  const parts = `${description}\0${JSON.stringify(extras)}`;
  const digest = createHash("sha256").update(parts).digest();
  return digest.readBigInt64BE(0);
}

function buildLocationCandidates(locations: string[] | null): string[] {
  if (!locations?.length) return [];
  const candidates: string[] = [];
  const seen = new Set<string>();

  for (const location of locations) {
    const trimmed = location.trim();
    if (!trimmed) continue;
    const parts = [
      trimmed,
      ...trimmed.split(",").map((part) => part.trim()),
    ];
    for (const part of parts) {
      const normalized = part.toLowerCase();
      if (!normalized || seen.has(normalized)) continue;
      seen.add(normalized);
      candidates.push(normalized);
    }
  }

  return candidates;
}

function normalizeDescriptionHtml(description: string | null): string | null {
  if (!description) return null;
  const trimmed = decodeEscapedHtml(description.trim());
  if (!trimmed) return null;

  const withoutDangerousTags = trimmed
    .replace(/<\s*(script|style|iframe|object|embed|svg|math|canvas|template|head)\b[\s\S]*?<\s*\/\s*\1\s*>/gi, "")
    .replace(/ on\w+="[^"]*"/gi, "")
    .replace(/ on\w+='[^']*'/gi, "")
    .replace(/javascript:/gi, "");

  return withoutDangerousTags || null;
}

function decodeEscapedHtml(value: string): string {
  if (!/&lt;\s*\/?\s*(?:p|h[1-6]|ul|ol|li|a|strong|em|b|i|u|s|br|blockquote|pre|code)\b/i.test(value)) {
    return value;
  }

  return value
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

function computeReverseDiff(nextHtml: string, previousHtml: string): string {
  return `--- new\n+++ old\n@@\n-${nextHtml}\n+${previousHtml}\n`;
}

function diffExtras(
  previous: Record<string, unknown>,
  next: Record<string, unknown>,
): Record<string, unknown> {
  const changed: Record<string, unknown> = {};
  const keys = new Set([...Object.keys(previous), ...Object.keys(next)]);
  for (const key of keys) {
    const previousValue = previous[key];
    const nextValue = next[key];
    if (JSON.stringify(previousValue) !== JSON.stringify(nextValue)) {
      changed[key] = previousValue ?? null;
    }
  }
  return changed;
}

function parseHistory(raw: string | null): {
  versions: Record<string, unknown>[];
  current_extras: Record<string, unknown>;
} {
  if (!raw) {
    return { versions: [], current_extras: {} };
  }

  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return {
      versions: Array.isArray(parsed.versions)
        ? parsed.versions.filter((value): value is Record<string, unknown> => !!value && typeof value === "object")
        : [],
      current_extras: asRecord(parsed.current_extras) ?? {},
    };
  } catch {
    return { versions: [], current_extras: {} };
  }
}

function asNonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asStringArray(value: unknown): string[] | null {
  if (!Array.isArray(value)) {
    return typeof value === "string" && value.trim() ? [value.trim()] : null;
  }

  const normalized = value
    .map((entry) => asNonEmptyString(entry))
    .filter((entry): entry is string => Boolean(entry));

  if (normalized.length === 0) return null;
  return [...new Set(normalized)];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

let r2Client: S3Client | null = null;

function getR2Client(): S3Client {
  if (r2Client) return r2Client;

  const endpoint = process.env.R2_ENDPOINT_URL;
  const accessKeyId = process.env.R2_ACCESS_KEY_ID;
  const secretAccessKey = process.env.R2_SECRET_ACCESS_KEY;

  if (!endpoint || !accessKeyId || !secretAccessKey) {
    throw new MetaApifyImportError("R2 credentials are not fully configured", 500);
  }

  r2Client = new S3Client({
    endpoint,
    region: "auto",
    credentials: {
      accessKeyId,
      secretAccessKey,
    },
  });

  return r2Client;
}

function getR2Bucket(): string {
  const bucket = process.env.R2_BUCKET;
  if (!bucket) throw new MetaApifyImportError("R2_BUCKET is not set", 500);
  return bucket;
}

async function getObjectText(key: string): Promise<string | null> {
  try {
    const response = await getR2Client().send(new GetObjectCommand({
      Bucket: getR2Bucket(),
      Key: key,
    }));
    if (!response.Body) return null;
    if ("transformToString" in response.Body && typeof response.Body.transformToString === "function") {
      return await response.Body.transformToString();
    }
    const chunks: Uint8Array[] = [];
    for await (const chunk of response.Body as AsyncIterable<Uint8Array>) {
      chunks.push(chunk);
    }
    return Buffer.concat(chunks).toString("utf-8");
  } catch (error) {
    if (isMissingObjectError(error)) return null;
    throw error;
  }
}

async function putObject(
  key: string,
  body: string,
  contentType: string,
): Promise<void> {
  await getR2Client().send(new PutObjectCommand({
    Bucket: getR2Bucket(),
    Key: key,
    Body: body,
    ContentType: contentType,
    CacheControl: "public, max-age=86400",
  }));
}

function isMissingObjectError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const code = "name" in error ? String(error.name) : "";
  return code === "NoSuchKey" || code === "NotFound";
}

const EMPLOYMENT_TYPE_MAP: Record<string, string> = {
  "full-time": "full_time",
  "full time": "full_time",
  full_time: "full_time",
  fulltime: "full_time",
  permanent: "full_time",
  "permanent employment": "full_time",
  "permanent full-time": "full_time",
  regular: "full_time",
  "employee / full-time": "full_time",
  "eor / full-time": "full_time",
  graduate: "full_time",
  other: "full_time",
  other_employment_type: "full_time",
  "part-time": "part_time",
  "part time": "part_time",
  part_time: "part_time",
  parttime: "part_time",
  contract: "contract",
  contractor: "contract",
  temporary: "contract",
  "temporary positions": "contract",
  "fixed term": "contract",
  "fixed term (fixed term)": "contract",
  "fixed term / full-time": "contract",
  internship: "internship",
  intern: "internship",
  "full time or part time": "full_or_part",
  "full-time, part-time": "full_or_part",
  "permanent full-time or part-time": "full_or_part",
  "temporary positions, full-time": "full_or_part",
  "full_time, part_time": "full_or_part",
};

const JOB_LOCATION_TYPE_MAP: Record<string, string> = {
  onsite: "onsite",
  "on-site": "onsite",
  "on site": "onsite",
  office: "onsite",
  "in-office": "onsite",
  "in office": "onsite",
  "on-premises": "onsite",
  "in-person": "onsite",
  "in person": "onsite",
  remote: "remote",
  telecommute: "remote",
  "work from home": "remote",
  wfh: "remote",
  "fully remote": "remote",
  "100% remote": "remote",
  hybrid: "hybrid",
  "office, remote": "hybrid",
  "remote, office": "hybrid",
  "office/remote": "hybrid",
  "remote/office": "hybrid",
  flexible: "hybrid",
  "partially remote": "hybrid",
};
