"use server";

import { sql, eq } from "drizzle-orm";
import { headers } from "next/headers";
import type { Octokit } from "@octokit/rest";
import { db } from "@/db";
import { companyRequest } from "@/db/schema";
import { companyRequestLimiter, getClientIp } from "@/lib/rate-limit";
import { isLocale } from "@/lib/i18n";

const INPUT_MIN_LENGTH = 2;
const INPUT_MAX_LENGTH = 200;

export type RequestCompanyResult = {
  success: boolean;
  errorCode?:
    | "empty"
    | "too_short"
    | "too_long"
    | "invalid"
    | "rate_limited"
    | "unknown";
  issueNumber?: number;
  issueCreationFailed?: boolean;
};

const TRACKING_PARAMS = /^(utm_\w+|ref|source|fbclid|gclid|mc_[a-z]+)$/i;

function normalizeUrl(raw: string): string {
  try {
    const url = new URL(raw);
    for (const key of [...url.searchParams.keys()]) {
      if (TRACKING_PARAMS.test(key)) {
        url.searchParams.delete(key);
      }
    }
    url.search = url.searchParams.toString() ? `?${url.searchParams}` : "";
    // Strip trailing slash from path (but keep "/" for bare domains)
    if (url.pathname.length > 1 && url.pathname.endsWith("/")) {
      url.pathname = url.pathname.replace(/\/+$/, "");
    }
    // Strip default ports
    url.port = "";
    // Strip fragment
    url.hash = "";
    return url.toString();
  } catch {
    return raw;
  }
}

function normalizeInput(raw: string): string {
  const trimmed = raw.trim().replace(/\s+/g, " ");
  // If it looks like a URL, normalize it (keep original casing for URLs)
  if (/^https?:\/\//i.test(trimmed)) {
    return normalizeUrl(trimmed);
  }
  return trimmed.toLowerCase();
}

function validateInput(input: string): RequestCompanyResult["errorCode"] | null {
  if (input.length < INPUT_MIN_LENGTH) return "too_short";
  if (input.length > INPUT_MAX_LENGTH) return "too_long";
  if (!/[a-zA-Z0-9]/.test(input)) return "invalid";
  return null;
}

/**
 * ISO 3166-1 alpha-2 shape: exactly two ASCII letters. We don't validate
 * against the full registered list (~250 entries, churns) because the country
 * is only used as denormalised context on the DB row + an additional bucket
 * on the rate-limit key — a structurally-valid garbage code degrades to "miss
 * a bucket", not "stuff arbitrary JSON into JSONB".
 */
const COUNTRY_CODE_RE = /^[A-Z]{2}$/;

/**
 * Build the `lastUserHint` JSONB blob from the form + request headers. The
 * shape is locked down to exactly two whitelisted keys (`country`, `locale`)
 * with each value validated, so an attacker who controls `accept-language` /
 * `cf-ipcountry` (or hand-crafts a Next-Action POST) cannot stuff arbitrary
 * keys/values into the row.
 *
 * Per issue #3235 — `lastUserHint` was previously a free-form Record<string,
 * string> populated directly from client-supplied headers.
 */
function buildUserHint(
  rawLocale: string | null,
  rawCountry: string | null,
): { country?: string; locale?: string } | null {
  const hint: { country?: string; locale?: string } = {};

  if (rawLocale && isLocale(rawLocale)) {
    hint.locale = rawLocale;
  }

  if (rawCountry) {
    // Normalize to uppercase before validating so "ch" and "CH" both pass.
    const upper = rawCountry.trim().toUpperCase();
    if (COUNTRY_CODE_RE.test(upper)) {
      hint.country = upper;
    }
  }

  return hint.country || hint.locale ? hint : null;
}

const GITHUB_REPO_OWNER = "colophon-group";
const GITHUB_REPO_NAME = "jobseek";

async function getOctokit(): Promise<Octokit | null> {
  const appId = process.env.GITHUB_APP_ID;
  const privateKey = process.env.GITHUB_APP_PRIVATE_KEY;
  const installationId = process.env.GITHUB_APP_INSTALLATION_ID;
  if (!appId || !privateKey || !installationId) return null;

  // Lazy-load octokit so the ~500-700 KB transitive dependency graph only
  // enters this server action's module evaluation when GitHub-issue creation
  // is actually required (#3193).
  const { Octokit } = await import("@octokit/rest");
  const { createAppAuth } = await import("@octokit/auth-app");

  return new Octokit({
    authStrategy: createAppAuth,
    auth: { appId, privateKey, installationId },
  });
}

type UserHint = { country?: string; locale?: string };

function buildIssueBody(
  input: string,
  userHint: UserHint | null,
): string {
  const lines: string[] = [
    "A user requested to add a company or fix an existing scraper.",
    "",
    "### User request",
    "",
    input,
    "",
  ];

  if (userHint && Object.keys(userHint).length > 0) {
    lines.push("### User context", "");
    if (userHint.country) lines.push(`- **Country:** ${userHint.country}`);
    if (userHint.locale) lines.push(`- **Language:** ${userHint.locale}`);
    lines.push("");
  }

  return lines.join("\n");
}

async function createGithubIssue(
  input: string,
  userHint: UserHint | null,
): Promise<{ issueNumber: number } | null> {
  const octokit = await getOctokit();
  if (!octokit) return null;

  try {
    const issue = await octokit.issues.create({
      owner: GITHUB_REPO_OWNER,
      repo: GITHUB_REPO_NAME,
      title: `Add company: ${input}`,
      body: buildIssueBody(input, userHint),
      labels: ["company-request"],
    });
    return { issueNumber: issue.data.number };
  } catch {
    return null;
  }
}

export async function requestCompany(
  _prev: RequestCompanyResult | null,
  formData: FormData,
): Promise<RequestCompanyResult> {
  // Layer 3 (issue #3235) — server-action header check. Next.js sets the
  // `Next-Action` header on every server-action POST. Direct CSRF-style hits
  // to the action endpoint without this header are not user-initiated form
  // submissions and should never reach the GH-issue / DB side effect.
  const h = await headers();
  if (!h.get("next-action")) {
    return { success: false, errorCode: "invalid" };
  }

  // Layer 2 (issue #3235) — derive validated, whitelisted user-hint up-front.
  // We need `country` to compose the rate-limit key (Layer 1) so attackers
  // can't cycle one axis to break out of the limit.
  const rawLocale = formData.get("locale");
  const rawCountry =
    h.get("x-vercel-ip-country") ?? h.get("cf-ipcountry") ?? null;
  const lastUserHint = buildUserHint(
    typeof rawLocale === "string" ? rawLocale : null,
    rawCountry,
  );

  // Layer 1 (issue #3235) — wire up `companyRequestLimiter`. The limiter was
  // defined (5/3600s per key) but never called, leaving the action open to
  // GitHub-issue-tracker DoS. Key composes the platform-authoritative IP
  // (via `getClientIp`, which is hardened against `x-forwarded-for`
  // spoofing per #3219) with the validated country code so an attacker who
  // cycles country headers still shares a bucket with their real IP.
  const ip = getClientIp(h);
  const rateKey = `${ip}:${lastUserHint?.country ?? "??"}`;
  const { success: allowed } = await companyRequestLimiter.limit(rateKey);
  if (!allowed) {
    return { success: false, errorCode: "rate_limited" };
  }

  const raw = (formData.get("input") as string | null)?.trim();
  const input = raw ? normalizeInput(raw) : null;
  if (!input) {
    return { success: false, errorCode: "empty" };
  }

  const errorCode = validateInput(input);
  if (errorCode) {
    return { success: false, errorCode };
  }

  try {
    // Check if this request already exists
    const [existing] = await db
      .select({
        id: companyRequest.id,
        githubIssueNumber: companyRequest.githubIssueNumber,
      })
      .from(companyRequest)
      .where(eq(companyRequest.input, input))
      .limit(1);

    if (existing) {
      // Increment count
      await db
        .update(companyRequest)
        .set({
          count: sql`${companyRequest.count} + 1`,
          lastUserHint,
          updatedAt: new Date(),
        })
        .where(eq(companyRequest.id, existing.id));

      // Backfill GitHub issue if missing
      if (!existing.githubIssueNumber) {
        const ghResult = await createGithubIssue(input, lastUserHint);
        if (ghResult) {
          await db
            .update(companyRequest)
            .set({ githubIssueNumber: ghResult.issueNumber })
            .where(eq(companyRequest.id, existing.id));
          return { success: true, issueNumber: ghResult.issueNumber };
        }
        return { success: true, issueCreationFailed: true };
      }

      return {
        success: true,
        issueNumber: existing.githubIssueNumber,
      };
    }

    // New request: insert into DB first
    const [inserted] = await db
      .insert(companyRequest)
      .values({ input, lastUserHint })
      .returning({ id: companyRequest.id });

    // Create GitHub issue
    const ghResult = await createGithubIssue(input, lastUserHint);

    if (ghResult) {
      await db
        .update(companyRequest)
        .set({ githubIssueNumber: ghResult.issueNumber })
        .where(eq(companyRequest.id, inserted.id));

      return { success: true, issueNumber: ghResult.issueNumber };
    }

    return { success: true, issueCreationFailed: true };
  } catch (err) {
    console.error("[requestCompany] failed:", err);
    return { success: false, errorCode: "unknown" };
  }
}
