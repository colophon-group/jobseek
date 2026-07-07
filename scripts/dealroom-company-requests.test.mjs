import assert from "node:assert/strict";
import test from "node:test";

import {
  auditDealroomEntries,
  buildIssueBody,
  buildIssueTitle,
  buildRegistryIndex,
  extractDealroomCompaniesFromHtml,
  issueTitleKey,
  normalizeHost,
  normalizeName,
  parseCreatedIssueResponse,
  parseCsvRows,
  parseListSitemapXml,
  slugify,
} from "./dealroom-company-requests.mjs";

const itemListHtml = `
  <html>
    <head>
      <script type="application/ld+json">{"@context":"https://schema.org","@type":"BreadcrumbList"}</script>
      <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "ItemList",
          "name": "AI startups in Europe",
          "itemListElement": [
            {
              "@type": "ListItem",
              "position": 1,
              "item": {
                "@type": "Organization",
                "name": "Mistral AI",
                "url": "https://www.mistral.ai/"
              }
            },
            {
              "@type": "ListItem",
              "position": 2,
              "item": {
                "@type": "Organization",
                "name": "ICEYE",
                "url": "https://www.iceye.com/"
              }
            }
          ]
        }
      </script>
    </head>
  </html>
`;

test("normalizes domains and slugs like the crawler registry", () => {
  assert.equal(normalizeHost("https://www.klarna.com/"), "klarna.com");
  assert.equal(normalizeHost("m.example.com/jobs"), "example.com");
  assert.equal(slugify("Anysphere | Cursor"), "anysphere-cursor");
  assert.equal(normalizeName("Monzo Bank"), "monzo");
});

test("parses CSV rows with quoted JSON extras", () => {
  const rows = parseCsvRows(
    'slug,name,website,extras\nstripe,Stripe,https://stripe.com,"{""sameAs"":[""x""]}"\n',
  );

  assert.deepEqual(rows, [
    {
      slug: "stripe",
      name: "Stripe",
      website: "https://stripe.com",
      extras: '{"sameAs":["x"]}',
    },
  ]);
});

test("extracts Dealroom companies from Schema.org ItemList JSON-LD", () => {
  const companies = extractDealroomCompaniesFromHtml(
    itemListHtml,
    "https://dealroom.co/lists/ai-startups-europe/",
  );

  assert.deepEqual(
    companies.map(({ name, website, host, slug, listTitle, position }) => ({
      name,
      website,
      host,
      slug,
      listTitle,
      position,
    })),
    [
      {
        name: "Mistral AI",
        website: "https://www.mistral.ai/",
        host: "mistral.ai",
        slug: "mistral-ai",
        listTitle: "AI startups in Europe",
        position: 1,
      },
      {
        name: "ICEYE",
        website: "https://www.iceye.com/",
        host: "iceye.com",
        slug: "iceye",
        listTitle: "AI startups in Europe",
        position: 2,
      },
    ],
  );
});

test("parses list sitemap locations", () => {
  const urls = parseListSitemapXml(`
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://dealroom.co/lists/ai-startups-europe/</loc></url>
      <url><loc>https://dealroom.co/lists/fintech-startups-europe/?a=1&amp;b=2</loc></url>
    </urlset>
  `);

  assert.deepEqual(urls, [
    "https://dealroom.co/lists/ai-startups-europe/",
    "https://dealroom.co/lists/fintech-startups-europe/?a=1&b=2",
  ]);
});

test("audits Dealroom entries against registry by host, slug, and normalized name", () => {
  const registry = buildRegistryIndex([
    { slug: "mistral-ai", name: "Mistral AI", website: "https://mistral.ai" },
    { slug: "monzo", name: "Monzo", website: "https://monzo.com" },
    { slug: "stripe", name: "Stripe", website: "https://stripe.com" },
  ]);
  const entries = [
    ...extractDealroomCompaniesFromHtml(itemListHtml, "https://dealroom.co/lists/ai-startups-europe/"),
    {
      name: "Monzo Bank",
      website: "https://monzo.example",
      host: "monzo.example",
      slug: "monzo-bank",
      nameKey: "monzo",
      listTitle: "Fintech startups in Europe",
      listUrl: "https://dealroom.co/lists/fintech-startups-europe/",
      position: 3,
    },
    {
      name: "ICEYE",
      website: "https://www.iceye.com/",
      host: "iceye.com",
      slug: "iceye",
      nameKey: "iceye",
      listTitle: "Space startups in Europe",
      listUrl: "https://dealroom.co/lists/space-tech-startups-europe/",
      position: 2,
    },
  ];

  const audit = auditDealroomEntries(entries, registry);

  assert.deepEqual(audit.stats, {
    uniqueCompanies: 3,
    matchedByHost: 1,
    matchedBySlug: 0,
    matchedByName: 1,
    missing: 1,
  });
  assert.equal(audit.missing[0].name, "ICEYE");
  assert.equal(audit.missing[0].lists.length, 2);
});

test("builds company-request issue title and body with parent evidence", () => {
  const company = {
    name: "ICEYE",
    website: "https://www.iceye.com/",
    slug: "iceye",
    lists: [
      {
        title: "Space startups in Europe",
        position: 2,
        url: "https://dealroom.co/lists/space-tech-startups-europe/",
      },
    ],
  };

  assert.equal(buildIssueTitle(company), "Add company: ICEYE");
  const body = buildIssueBody(company, { parentIssue: "3570" });

  assert.match(body, /Source:\*\* Dealroom top lists \(#3570\)/);
  assert.match(body, /Company name:\*\* ICEYE/);
  assert.match(body, /Space startups in Europe #2/);
  assert.match(body, /Parent tracking issue: #3570/);
});

test("normalizes issue titles for idempotent company-request dedupe", () => {
  assert.equal(issueTitleKey(" Add company: ICEYE "), "add company: iceye");
  assert.equal(issueTitleKey(null), "");
});

test("parses and validates GitHub issue create responses", () => {
  assert.deepEqual(
    parseCreatedIssueResponse(
      JSON.stringify({
        number: 123,
        title: "Add company: ICEYE",
        html_url: "https://github.com/colophon-group/jobseek/issues/123",
      }),
      "Add company: ICEYE",
    ),
    {
      number: 123,
      title: "Add company: ICEYE",
      url: "https://github.com/colophon-group/jobseek/issues/123",
    },
  );

  assert.throws(
    () => parseCreatedIssueResponse("", "Add company: ICEYE"),
    /Could not parse GitHub issue create response/,
  );
  assert.throws(
    () => parseCreatedIssueResponse(JSON.stringify({ number: 123 }), "Add company: ICEYE"),
    /did not include html_url/,
  );
  assert.throws(
    () =>
      parseCreatedIssueResponse(
        JSON.stringify({
          number: 123,
          title: "Add company: Different",
          html_url: "https://github.com/colophon-group/jobseek/issues/123",
        }),
        "Add company: ICEYE",
      ),
    /title mismatch/,
  );
});
