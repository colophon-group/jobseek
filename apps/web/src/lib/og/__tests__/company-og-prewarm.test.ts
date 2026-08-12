import { ListObjectsV2Command, PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  renderSiteOgCard: vi.fn(),
}));

vi.mock("@/lib/og/site-og-card", () => ({
  renderSiteOgCard: mocks.renderSiteOgCard,
}));

import {
  buildCompanyDocuments,
  parseOptions,
  prewarmSiteOgCard,
  withRetry,
} from "../../../../script/prewarm-company-og-cache";
import { SITE_OG_KEY } from "../site-og-key";

describe("company OG prewarm CLI", () => {
  it("keeps an existing immutable site-wide card", async () => {
    const send = vi.fn().mockResolvedValue({
      Contents: [{ Key: SITE_OG_KEY }],
      IsTruncated: false,
    });
    const client = { send } as unknown as S3Client;

    await expect(prewarmSiteOgCard(client, "test-bucket", false))
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

    await expect(prewarmSiteOgCard(client, "test-bucket", false))
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
      force: false,
      locales: ["en", "de"],
      maxCompanies: 25,
      rendererVersion: null,
      yes: true,
    });
  });

  it("rejects invalid concurrency before touching external services", () => {
    expect(() => parseOptions(["--concurrency", "0"])).toThrow(
      "--concurrency must be a positive integer",
    );
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
});
