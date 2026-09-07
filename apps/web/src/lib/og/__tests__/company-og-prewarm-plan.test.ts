import { describe, expect, it } from "vitest";
import { buildCompanyDocuments } from "../../../../script/prewarm-company-og-cache";
import {
  planCompanyOgPrewarm,
  type CompanyOgDocument,
} from "../company-og-prewarm-plan";

const locales = ["en", "de", "fr", "it"];

function company(slug: string, name = slug): CompanyOgDocument {
  return { id: slug, slug, name, active_posting_count: 0 };
}

function keys(...slugs: string[]): Set<string> {
  return new Set(slugs.flatMap((slug) =>
    locales.map((locale) => `og/company/render-v1/${locale}/${slug}.png`)
  ));
}

function plan(
  baseDocuments: CompanyOgDocument[],
  targetDocuments: CompanyOgDocument[],
  existingKeys = keys(...targetDocuments.map(({ slug }) => slug)),
) {
  return planCompanyOgPrewarm({
    baseDocuments,
    targetDocuments,
    existingKeys,
    rendererVersion: "render-v1",
    locales,
    fullRebuild: false,
  });
}

describe("company OG incremental planning", () => {
  it("plans exactly four cards for one changed company", () => {
    const result = plan(
      [company("acme", "Old Acme"), company("stable")],
      [company("acme", "New Acme"), company("stable")],
    );

    expect(result.changedSlugs).toEqual(["acme"]);
    expect(result.tasks).toHaveLength(4);
    expect(new Set(result.tasks.map(({ slug }) => slug))).toEqual(new Set(["acme"]));
    expect(result.tasks.every(({ reason }) => reason === "changed")).toBe(true);
  });

  it("covers additions and skipped revisions while removals need no render", () => {
    const result = plan(
      [company("changed", "v1"), company("removed")],
      [company("changed", "v3"), company("added")],
    );

    expect(result.changedSlugs).toEqual(["added", "changed"]);
    expect(result.removedSlugs).toEqual(["removed"]);
    expect(result.tasks).toHaveLength(8);
    expect(result.tasks.some(({ slug }) => slug === "removed")).toBe(false);
  });

  it("reconciles a missing key without rewriting present unchanged keys", () => {
    const existing = keys("acme", "stable");
    existing.delete("og/company/render-v1/fr/stable.png");
    const result = plan(
      [company("acme"), company("stable")],
      [company("acme"), company("stable")],
      existing,
    );

    expect(result.changedSlugs).toEqual([]);
    expect(result.tasks).toEqual([{
      company: company("stable"),
      locale: "fr",
      slug: "stable",
      reason: "missing",
    }]);
  });

  it("ignores row reordering and unused industry-keyword churn", () => {
    const companies = [
      "slug,name,website,logo_url,icon_url,logo_type,industry,employee_count_range,founded_year,extras",
      "acme,Acme,https://acme.test,,,,1,,,",
      "beta,Beta,https://beta.test,,,,2,,,",
    ];
    const descriptions = "slug,en,de,fr,it\nacme,Hello,,,\nbeta,World,,,";
    const base = buildCompanyDocuments(
      companies.join("\n"),
      descriptions,
      "id,name,keywords\n1,Technology,software\n2,Finance,money",
      null,
    );
    const target = buildCompanyDocuments(
      [companies[0], companies[2], companies[1]].join("\n"),
      descriptions,
      "id,name,keywords\n1,Technology,cloud;ai\n2,Finance,banking",
      null,
    );

    expect(plan(base, target).tasks).toEqual([]);
  });

  it("ignores source fields that the card does not render", () => {
    expect(plan(
      [{ ...company("acme"), employee_count_range: 2, founded_year: 1999 }],
      [{ ...company("acme"), employee_count_range: 5, founded_year: 2005 }],
    ).tasks).toEqual([]);
  });

  it("fans an industry-name change out only to referencing companies", () => {
    const companies = [
      "slug,name,website,logo_url,icon_url,logo_type,industry,employee_count_range,founded_year,extras",
      "acme,Acme,,,,,1,,,",
      "beta,Beta,,,,,2,,,",
    ].join("\n");
    const descriptions = "slug,en,de,fr,it\nacme,Hello,,,\nbeta,World,,,";
    const base = buildCompanyDocuments(
      companies,
      descriptions,
      "id,name,keywords\n1,Technology,software\n2,Finance,money",
      null,
    );
    const target = buildCompanyDocuments(
      companies,
      descriptions,
      "id,name,keywords\n1,Deep Tech,software\n2,Finance,money",
      null,
    );
    const result = plan(base, target);

    expect(result.changedSlugs).toEqual(["acme"]);
    expect(result.tasks).toHaveLength(4);
  });

  it("makes an explicitly approved full rebuild a bounded, explicit plan", () => {
    const result = planCompanyOgPrewarm({
      baseDocuments: [],
      targetDocuments: [company("acme"), company("beta")],
      existingKeys: keys("acme", "beta"),
      rendererVersion: "render-v1",
      locales,
      fullRebuild: true,
      maxCompanies: 1,
    });

    expect(result.tasks).toHaveLength(4);
    expect(new Set(result.tasks.map(({ slug }) => slug))).toEqual(new Set(["acme"]));
    expect(result.tasks.every(({ reason }) => reason === "full-rebuild")).toBe(true);
  });
});
