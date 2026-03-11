"use server";

import { sql, eq } from "drizzle-orm";
import { headers } from "next/headers";
import { Octokit } from "@octokit/rest";
import { createAppAuth } from "@octokit/auth-app";
import { db } from "@/db";
import { company, companyRequest, jobPosting } from "@/db/schema";
import { cached } from "@/lib/cache";

export async function getStats() {
  return cached(
    "platform-stats",
    async () => {
      const [[companyRow], [jobRow]] = await Promise.all([
        db.select({ count: sql<number>`count(*)` }).from(company),
        db
          .select({ count: sql<number>`count(*)` })
          .from(jobPosting)
          .where(sql`${jobPosting.isActive} = true`),
      ]);
      return {
        companyCount: Number(companyRow.count),
        jobPostingCount: Number(jobRow.count),
      };
    },
    { ttl: 21600 }, // 6 hours
  );
}

const INPUT_MIN_LENGTH = 2;
const INPUT_MAX_LENGTH = 200;

export type RequestCompanyResult = {
  success: boolean;
  errorCode?: "empty" | "too_short" | "too_long" | "invalid" | "unknown";
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

async function buildUserHint(formData: FormData) {
  const h = await headers();
  const locale = (formData.get("locale") as string | null) || undefined;
  const country =
    h.get("x-vercel-ip-country") ?? h.get("cf-ipcountry") ?? undefined;

  const hint: Record<string, string> = {};
  if (locale) hint.locale = locale;
  if (country) hint.country = country;
  return Object.keys(hint).length ? hint : null;
}

const GITHUB_REPO_OWNER = "colophon-group";
const GITHUB_REPO_NAME = "jobseek";

function getOctokit(): Octokit | null {
  const appId = process.env.GITHUB_APP_ID;
  const privateKey = process.env.GITHUB_APP_PRIVATE_KEY;
  const installationId = process.env.GITHUB_APP_INSTALLATION_ID;
  if (!appId || !privateKey || !installationId) return null;

  return new Octokit({
    authStrategy: createAppAuth,
    auth: { appId, privateKey, installationId },
  });
}

function buildIssueBody(
  input: string,
  userHint: Record<string, string> | null,
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
  userHint: Record<string, string> | null,
): Promise<{ issueNumber: number } | null> {
  const octokit = getOctokit();
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
  const raw = (formData.get("input") as string | null)?.trim();
  const input = raw ? normalizeInput(raw) : null;
  if (!input) {
    return { success: false, errorCode: "empty" };
  }

  const errorCode = validateInput(input);
  if (errorCode) {
    return { success: false, errorCode };
  }

  const lastUserHint = await buildUserHint(formData);

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
