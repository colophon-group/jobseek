import { ListObjectsV2Command, PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  renderSiteOgCard: vi.fn(),
}));

vi.mock("@/lib/og/site-og-card", () => ({
  renderSiteOgCard: mocks.renderSiteOgCard,
}));

import {
  assertWritePlanWithinBudget,
  buildCompanyDocuments,
  createR2Client,
  isAncestorRevision,
  parseOptions,
  prewarmSiteOgCard,
  publishCompanyOgCompletionMarkers,
  PutAttemptBudget,
  readPublishedBaseline,
  shouldPublishCompanyOgCompletion,
  withRetry,
} from "../../../../script/prewarm-company-og-cache";
import { SITE_OG_KEY } from "../site-og-key";
import { planCompanyOgPrewarm } from "../company-og-prewarm-plan";

describe("company OG prewarm CLI", () => {
  it("keeps an existing immutable site-wide card", async () => {
    const send = vi.fn().mockResolvedValue({
      Contents: [{ Key: SITE_OG_KEY }],
      IsTruncated: false,
    });
    const client = { send } as unknown as S3Client;

    await expect(prewarmSiteOgCard(client, "test-bucket"))
      .resolves.toBe("existing");
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]?.[0]).toBeInstanceOf(ListObjectsV2Command);
    expect(mocks.renderSiteOgCard).not.toHaveBeenCalled();
  });

  it("uploads a missing site-wide card with immutable cache headers", async () => {
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3, 4, 5]);
    mocks.renderSiteOgCard.mockResolvedValue(new Response(png));
    const send = vi.fn()
      .mockResolvedValueOnce({ Contents: [], IsTruncated: false })
      .mockResolvedValueOnce({});
    const client = { send } as unknown as S3Client;

    await expect(prewarmSiteOgCard(client, "test-bucket"))
      .resolves.toBe("uploaded");
    expect(send).toHaveBeenCalledTimes(2);
    const upload = send.mock.calls[1]?.[0];
    expect(upload).toBeInstanceOf(PutObjectCommand);
    expect((upload as PutObjectCommand).input).toMatchObject({
      Bucket: "test-bucket",
      Key: SITE_OG_KEY,
      ContentType: "image/png",
      CacheControl: "public, max-age=31536000, immutable",
    });
  });

  it("builds localized card data from the repository sources", () => {
    const documents = buildCompanyDocuments(
      [
        "slug,name,website,logo_url,icon_url,logo_type,industry,employee_count_range,founded_year,extras",
        "acme,Acme,https://acme.test,,https://assets.test/acme.png,icon,1,3,1999,",
      ].join("\n"),
      [
        "slug,en,de,fr,it",
        "acme,English description,Deutsche Beschreibung,,",
      ].join("\n"),
      ["id,name,keywords", "1,Technology,software"].join("\n"),
      null,
    );

    expect(documents).toEqual([{
      id: "acme",
      name: "Acme",
      slug: "acme",
      active_posting_count: 0,
      icon: "https://assets.test/acme.png",
      website: "https://acme.test",
      industry_id: 1,
      industry_name: "Technology",
      employee_count_range: 3,
      founded_year: 1999,
      description: "English description",
      description_de: "Deutsche Beschreibung",
    }]);
  });

  it("loads and validates every production company source row", async () => {
    const { readFile } = await import("node:fs/promises");
    const companies = await readFile("../crawler/data/companies.csv", "utf8");
    const descriptions = await readFile(
      "../crawler/data/company_descriptions.csv",
      "utf8",
    );
    const industries = await readFile("../crawler/data/industries.csv", "utf8");

    const documents = buildCompanyDocuments(
      companies,
      descriptions,
      industries,
      null,
    );

    expect(documents.length).toBeGreaterThan(5_000);
    expect(new Set(documents.map((company) => company.slug)).size)
      .toBe(documents.length);
  });

  it("parses bounded canary options passed through pnpm", () => {
    expect(parseOptions([
      "--",
      "--yes",
      "--concurrency",
      "3",
      "--max-companies",
      "25",
      "--locales",
      "en,de",
    ])).toEqual({
      concurrency: 3,
      fullRebuild: false,
      fullRebuildConfirmation: null,
      locales: ["en", "de"],
      maxCompanies: 25,
      maxPlannedWrites: 1000,
      maxPutAttempts: 3000,
      rendererVersion: null,
      targetRevision: null,
      yes: true,
    });
  });

  it("requires an explicit phrase for a bounded full rebuild", () => {
    expect(() => parseOptions(["--full-rebuild"])).toThrow(
      "--full-rebuild requires --approve-full-rebuild REBUILD-COMPANY-OG",
    );
    expect(parseOptions([
      "--full-rebuild",
      "--approve-full-rebuild",
      "REBUILD-COMPANY-OG",
      "--max-planned-writes",
      "30000",
      "--max-put-attempts",
      "90000",
    ])).toMatchObject({
      fullRebuild: true,
      maxPlannedWrites: 30_000,
      maxPutAttempts: 90_000,
    });
  });

  it("rejects invalid concurrency before touching external services", () => {
    expect(() => parseOptions(["--concurrency", "0"])).toThrow(
      "--concurrency must be a positive integer",
    );
    expect(() => parseOptions(["--concurrency", "5"])).toThrow(
      "--concurrency must be between 1 and 4",
    );
  });

  it("disables hidden AWS SDK retries so every request is metered", async () => {
    vi.stubEnv("R2_ENDPOINT_URL", "https://r2.example.test");
    vi.stubEnv("R2_ACCESS_KEY_ID", "test-access");
    vi.stubEnv("R2_SECRET_ACCESS_KEY", "test-secret");
    vi.stubEnv("R2_BUCKET", "test-bucket");
    const { client } = createR2Client();

    await expect(client.config.maxAttempts()).resolves.toBe(1);
    client.destroy();
    vi.unstubAllEnvs();
  });

  it("rejects a published revision that is not an ancestor of the target", () => {
    const repository = mkdtempSync(join(tmpdir(), "company-og-git-"));
    const git = (...args: string[]) => execFileSync("git", args, {
      cwd: repository,
      encoding: "utf8",
    }).trim();

    try {
      git("init", "--quiet");
      git("config", "user.email", "company-og-test@example.com");
      git("config", "user.name", "Company OG test");
      git("commit", "--allow-empty", "--quiet", "-m", "base");
      const base = git("rev-parse", "HEAD");

      git("checkout", "--quiet", "-b", "left");
      git("commit", "--allow-empty", "--quiet", "-m", "left");
      const left = git("rev-parse", "HEAD");

      git("checkout", "--quiet", "-b", "right", base);
      git("commit", "--allow-empty", "--quiet", "-m", "right");
      const right = git("rev-parse", "HEAD");

      expect(isAncestorRevision(repository, base, left)).toBe(true);
      expect(isAncestorRevision(repository, left, right)).toBe(false);
    } finally {
      rmSync(repository, { recursive: true, force: true });
    }
  });

  it("accepts only current pointers backed by matching immutable coverage", async () => {
    const immutable = {
      schemaVersion: 2,
      complete: true,
      rendererVersion: "render-v1",
      sourceVersion: "source-v1",
      revision: "a".repeat(40),
      baseRevision: null,
      companies: 2,
      locales: ["en", "de", "fr", "it"],
      expected: 8,
      completedAt: "2026-09-06T00:00:00.000Z",
    };
    const current = {
      ...immutable,
      revision: "c".repeat(40),
      baseRevision: "b".repeat(40),
      completedAt: "2026-09-07T00:00:00.000Z",
    };
    const send = vi.fn()
      .mockResolvedValueOnce({
        Body: { transformToString: async () => JSON.stringify(current) },
      })
      .mockResolvedValueOnce({
        Body: { transformToString: async () => JSON.stringify(immutable) },
      });

    await expect(readPublishedBaseline(
      { send } as unknown as S3Client,
      "test-bucket",
      "render-v1",
    )).resolves.toEqual({ marker: current, problem: null });
  });

  it("fails closed on a malformed baseline without issuing a PUT", async () => {
    const send = vi.fn().mockResolvedValueOnce({
      Body: { transformToString: async () => "not-json" },
    });

    await expect(readPublishedBaseline(
      { send } as unknown as S3Client,
      "test-bucket",
      "render-v1",
    )).resolves.toEqual({
      marker: null,
      problem: "current completion marker is legacy or malformed",
    });
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls.some(([command]) => command instanceof PutObjectCommand))
      .toBe(false);
  });

  it("requires a bootstrap when the baseline is missing without issuing a PUT", async () => {
    const missing = Object.assign(new Error("missing"), { name: "NoSuchKey" });
    const send = vi.fn().mockRejectedValueOnce(missing);

    await expect(readPublishedBaseline(
      { send } as unknown as S3Client,
      "test-bucket",
      "render-v1",
    )).resolves.toEqual({
      marker: null,
      problem: "current completion marker is missing",
    });
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls.some(([command]) => command instanceof PutObjectCommand))
      .toBe(false);
  });

  it("never plans marker publication after partial failure or for a canary", () => {
    expect(shouldPublishCompanyOgCompletion({
      failureCount: 1,
      isFullMatrix: true,
      baselineSourceVersion: "source-v1",
      targetSourceVersion: "source-v2",
    })).toBe(false);
    expect(shouldPublishCompanyOgCompletion({
      failureCount: 0,
      isFullMatrix: false,
      baselineSourceVersion: "source-v1",
      targetSourceVersion: "source-v2",
    })).toBe(false);
    expect(shouldPublishCompanyOgCompletion({
      failureCount: 0,
      isFullMatrix: true,
      baselineSourceVersion: "source-v1",
      targetSourceVersion: "source-v2",
    })).toBe(true);
  });

  it("retries an upload failure and surfaces the terminal error", async () => {
    const failure = new Error("simulated R2 failure");
    const operation = vi.fn().mockRejectedValue(failure);

    await expect(withRetry(operation, 0)).rejects.toBe(failure);
    expect(operation).toHaveBeenCalledTimes(3);
  });

  it("returns after the first successful upload attempt", async () => {
    const operation = vi.fn().mockResolvedValue("uploaded");

    await expect(withRetry(operation, 0)).resolves.toBe("uploaded");
    expect(operation).toHaveBeenCalledTimes(1);
  });

  it("rejects an oversized plan before an external operation can begin", () => {
    const documents = Array.from({ length: 251 }, (_, index) => ({
      slug: `company-${String(index).padStart(3, "0")}`,
      name: `Company ${index}`,
    }));
    const plan = planCompanyOgPrewarm({
      targetDocuments: documents,
      baseDocuments: [],
      existingKeys: new Set(),
      rendererVersion: "render-v1",
      locales: ["en", "de", "fr", "it"],
      fullRebuild: false,
    });
    const send = vi.fn();

    expect(() => {
      assertWritePlanWithinBudget(plan.tasks.length + 2, 1000, 3000);
      send(new PutObjectCommand({ Bucket: "test-bucket", Key: "forbidden" }));
    }).toThrow("zero PUTs performed");
    expect(plan.tasks).toHaveLength(1004);
    expect(send).not.toHaveBeenCalled();
  });

  it("counts retries against the runtime PUT-attempt budget", async () => {
    const budget = new PutAttemptBudget(2);
    const send = vi.fn().mockRejectedValue(new Error("R2 unavailable"));

    await expect(withRetry(async () => {
      budget.consume("og/company/v1/en/acme.png");
      await send();
    }, 0)).rejects.toThrow("PUT-attempt budget exhausted");
    expect(send).toHaveBeenCalledTimes(2);
    expect(budget.used).toBe(2);
  });

  it("never advances current.json when the immutable marker fails", async () => {
    const send = vi.fn().mockRejectedValue(new Error("R2 unavailable"));
    const client = { send } as unknown as S3Client;

    await expect(publishCompanyOgCompletionMarkers({
      client,
      bucket: "test-bucket",
      budget: new PutAttemptBudget(10),
      reuseImmutable: false,
      marker: {
        schemaVersion: 2,
        complete: true,
        rendererVersion: "render-v1",
        sourceVersion: "source-v1",
        revision: "a".repeat(40),
        baseRevision: "b".repeat(40),
        companies: 1,
        locales: ["en", "de", "fr", "it"],
        expected: 4,
        completedAt: "2026-09-07T00:00:00.000Z",
      },
    })).rejects.toThrow("R2 unavailable");
    expect(send).toHaveBeenCalledTimes(3);
    for (const [command] of send.mock.calls) {
      expect(command).toBeInstanceOf(PutObjectCommand);
      expect((command as PutObjectCommand).input.Key).toBe(
        "og/company/render-v1/_complete/source-v1.json",
      );
    }
  });

  it("fails the publication when current.json cannot advance", async () => {
    const failure = new Error("current pointer unavailable");
    const send = vi.fn()
      .mockResolvedValueOnce({})
      .mockRejectedValue(failure);
    const client = { send } as unknown as S3Client;

    await expect(publishCompanyOgCompletionMarkers({
      client,
      bucket: "test-bucket",
      budget: new PutAttemptBudget(10),
      reuseImmutable: false,
      marker: {
        schemaVersion: 2,
        complete: true,
        rendererVersion: "render-v1",
        sourceVersion: "source-v1",
        revision: "a".repeat(40),
        baseRevision: "b".repeat(40),
        companies: 1,
        locales: ["en", "de", "fr", "it"],
        expected: 4,
        completedAt: "2026-09-07T00:00:00.000Z",
      },
    })).rejects.toBe(failure);
    const keys = send.mock.calls.map(([command]) =>
      (command as PutObjectCommand).input.Key
    );
    expect(keys).toEqual([
      "og/company/render-v1/_complete/source-v1.json",
      "og/company/render-v1/_complete/current.json",
      "og/company/render-v1/_complete/current.json",
      "og/company/render-v1/_complete/current.json",
    ]);
  });

  it("publishes the immutable marker before an identical current pointer", async () => {
    const send = vi.fn().mockResolvedValue({});
    const client = { send } as unknown as S3Client;
    const marker = {
      schemaVersion: 2 as const,
      complete: true as const,
      rendererVersion: "render-v1",
      sourceVersion: "source-v2",
      revision: "c".repeat(40),
      baseRevision: "b".repeat(40),
      companies: 1,
      locales: ["en", "de", "fr", "it"],
      expected: 4,
      completedAt: "2026-09-07T00:00:00.000Z",
    };

    await expect(publishCompanyOgCompletionMarkers({
      client,
      bucket: "test-bucket",
      budget: new PutAttemptBudget(2),
      marker,
      reuseImmutable: false,
    })).resolves.toEqual({
      completionMarker: "og/company/render-v1/_complete/source-v2.json",
      currentMarker: "og/company/render-v1/_complete/current.json",
    });
    const [immutable, current] = send.mock.calls.map(([command]) =>
      (command as PutObjectCommand).input
    );
    expect(immutable.Key).toBe(
      "og/company/render-v1/_complete/source-v2.json",
    );
    expect(current.Key).toBe(
      "og/company/render-v1/_complete/current.json",
    );
    expect(current.Body).toBe(immutable.Body);
  });

  it("meters exactly one successful client request per marker PUT", async () => {
    const send = vi.fn().mockResolvedValue({});
    const budget = new PutAttemptBudget(1);

    await expect(publishCompanyOgCompletionMarkers({
      client: { send } as unknown as S3Client,
      bucket: "test-bucket",
      budget,
      reuseImmutable: true,
      marker: {
        schemaVersion: 2,
        complete: true,
        rendererVersion: "render-v1",
        sourceVersion: "source-v1",
        revision: "a".repeat(40),
        baseRevision: null,
        companies: 1,
        locales: ["en", "de", "fr", "it"],
        expected: 4,
        completedAt: "2026-09-07T00:00:00.000Z",
      },
    })).resolves.toEqual({
      completionMarker: "og/company/render-v1/_complete/source-v1.json",
      currentMarker: "og/company/render-v1/_complete/current.json",
    });
    expect(send).toHaveBeenCalledTimes(1);
    expect(budget.used).toBe(1);
  });
});
