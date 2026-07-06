import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  importLatestMetaApifyRun,
  mapApifyDatasetItems,
  normalizeDescriptionHtml,
  type MetaApifyImportDeps,
} from "../meta-apify-import";

describe("mapApifyDatasetItems", () => {
  it("maps Apify items, skips rows without urls, and deduplicates by url", () => {
    const result = mapApifyDatasetItems([
      {
        title: "Missing URL",
      },
      {
        url: "https://example.com/jobs/1",
        title: "First title",
        teams: ["Engineering"],
        subTeams: ["Infra"],
        responsibilities: "Build systems",
      },
      {
        url: "https://example.com/jobs/1",
        title: "Updated title",
        qualifications: "TypeScript",
      },
    ]);

    expect(result.skippedMissingUrl).toBe(1);
    expect(result.jobs).toHaveLength(1);
    expect(result.jobs[0]).toMatchObject({
      url: "https://example.com/jobs/1",
      title: "Updated title",
      extras: { qualifications: "TypeScript" },
    });
  });
});

describe("importLatestMetaApifyRun", () => {
  it("inserts new jobs, updates existing jobs, and tracks R2 writes", async () => {
    const insertPosting = vi.fn(async ({ sourceUrl }: { sourceUrl: string }) => ({
      id: sourceUrl.endsWith("/new") ? "posting-new" : "posting-other",
    }));
    const updatePosting = vi.fn(async () => undefined);
    const updatePostingHash = vi.fn(async () => undefined);
    const persistDescription = vi
      .fn<MetaApifyImportDeps["persistDescription"]>()
      .mockResolvedValueOnce({ status: "uploaded", hash: BigInt(1) })
      .mockResolvedValueOnce({ status: "unchanged", hash: BigInt(9) });

    const deps: MetaApifyImportDeps = {
      now: () => new Date("2026-03-14T12:00:00.000Z"),
      getBoardConfig: async () => ({
        boardId: "board-1",
        boardSlug: "meta-careers",
        companyId: "company-1",
        actorId: "actor-1",
      }),
      getLatestDataset: async () => ({
        actorId: "actor-1",
        runId: "run-1",
        datasetId: "dataset-1",
        items: [
          {
            url: "https://jobs.example.com/new",
            title: "New role",
            description: "<p>Hello</p>",
          },
          {
            url: "https://jobs.example.com/existing",
            title: "Existing role",
            description: "<p>Updated</p>",
          },
        ],
      }),
      getExistingPostings: async () =>
        new Map([
          [
            "https://jobs.example.com/existing",
            { id: "posting-existing", descriptionR2Hash: BigInt(9) },
          ],
        ]),
      insertPosting,
      updatePosting,
      updatePostingHash,
      resolveLocations: async () => ({ locationIds: null, locationTypes: null }),
      persistDescription,
    };

    const result = await importLatestMetaApifyRun(deps);

    expect(result).toMatchObject({
      fetched: 2,
      inserted: 1,
      updated: 1,
      r2Uploaded: 1,
      r2Unchanged: 1,
      skippedMissingUrl: 0,
    });
    expect(insertPosting).toHaveBeenCalledTimes(1);
    expect(updatePosting).toHaveBeenCalledTimes(1);
    expect(updatePostingHash).toHaveBeenCalledTimes(1);
  });
});

describe("normalizeDescriptionHtml", () => {
  it("returns null for empty input", () => {
    expect(normalizeDescriptionHtml(null)).toBeNull();
    expect(normalizeDescriptionHtml("")).toBeNull();
    expect(normalizeDescriptionHtml("   ")).toBeNull();
  });

  it("preserves innocuous semantic HTML", () => {
    const html =
      "<p>Hello <strong>world</strong></p><ul><li>item one</li><li>item two</li></ul>";
    expect(normalizeDescriptionHtml(html)).toBe(html);
  });

  it("preserves heading tags", () => {
    const html = "<h1>Title</h1><h2>Subtitle</h2><h3>Section</h3>";
    expect(normalizeDescriptionHtml(html)).toBe(html);
  });

  it("strips anchor tag attributes (href) while keeping the tag", () => {
    // ALLOWED_ATTR is [] for parity with sanitizeJobHtml.
    expect(
      normalizeDescriptionHtml('<a href="https://example.com">link</a>'),
    ).toBe("<a>link</a>");
  });

  it("strips inline style attributes", () => {
    expect(normalizeDescriptionHtml('<p style="color:red">text</p>')).toBe(
      "<p>text</p>",
    );
  });

  // #3229 bypass 1: unclosed <script> survived the old regex because it
  // required a paired </script>.
  it("strips unclosed script tags (#3229 bypass)", () => {
    const out = normalizeDescriptionHtml(
      '<p>ok</p><script src="//evil/a.js">',
    );
    expect(out).not.toMatch(/<script/i);
    expect(out).not.toMatch(/evil/);
    expect(out).toContain("<p>ok</p>");
  });

  // #3229 bypass 2: self-closing svg with unquoted onload survived because
  // the old `on\w+="..."` regex required quoted values.
  it("strips svg + unquoted on* attributes (#3229 bypass)", () => {
    const out = normalizeDescriptionHtml(
      "<p>before</p><svg onload=alert(1) /><p>after</p>",
    );
    expect(out).not.toMatch(/<svg/i);
    expect(out).not.toMatch(/onload/i);
    expect(out).not.toMatch(/alert/);
  });

  // #3229 bypass 3: img with unquoted onerror, similar shape.
  it("strips img tags entirely (#3229 bypass — not in allowlist)", () => {
    const out = normalizeDescriptionHtml(
      "<p>ok</p><img src=x onerror=alert(1)>",
    );
    expect(out).not.toMatch(/<img/i);
    expect(out).not.toMatch(/onerror/i);
    expect(out).not.toMatch(/alert/);
    expect(out).toContain("<p>ok</p>");
  });

  // #3229 bypass 4: nested tag splice — old regex would unwrap to <script>.
  // DOMPurify breaks the splice apart so any leftover "script"/"alert" text
  // is inert (e.g. as text content or entity-escaped) — the security
  // property we care about is that no executable <script> element survives.
  it("handles nested-tag splice attempts (#3229 bypass)", () => {
    const out = normalizeDescriptionHtml(
      "<p>ok</p><scr<script>ipt>alert(1)</scr</script>ipt>",
    );
    expect(out).not.toMatch(/<script/i);
    // No raw `<` followed by an alphabetic char that could re-form a tag.
    expect(out).not.toMatch(/<[a-zA-Z]+\s*on/i);
    // Still keeps the innocuous paragraph.
    expect(out).toContain("<p>ok</p>");
  });

  // #3229 bypass 5: data: URLs were not blocked by the old `javascript:`
  // substring filter.
  it("strips data: URLs in anchor href (#3229 bypass)", () => {
    const out = normalizeDescriptionHtml(
      '<a href="data:text/html,<script>alert(1)</script>">click</a>',
    );
    expect(out).not.toMatch(/data:/i);
    expect(out).not.toMatch(/<script/i);
    expect(out).not.toMatch(/alert/);
  });

  // #3229 bypass 6: `javascript:` URL handling — DOMPurify removes this
  // attribute by default; even without href on the allowlist, the URL
  // itself must not survive.
  it("strips javascript: URLs (#3229 bypass)", () => {
    const out = normalizeDescriptionHtml(
      '<a href="javascript:alert(1)">link</a>',
    );
    expect(out).not.toMatch(/javascript:/i);
    expect(out).not.toMatch(/alert/);
  });

  it("strips iframe tags entirely", () => {
    const out = normalizeDescriptionHtml(
      '<p>ok</p><iframe src="https://evil.example/"></iframe>',
    );
    expect(out).not.toMatch(/<iframe/i);
    expect(out).not.toMatch(/evil/);
  });

  it("strips style tags entirely", () => {
    const out = normalizeDescriptionHtml(
      "<p>ok</p><style>body{display:none}</style>",
    );
    expect(out).not.toMatch(/<style/i);
    expect(out).not.toMatch(/display:none/);
  });

  it("strips object and embed tags", () => {
    const out = normalizeDescriptionHtml(
      '<p>ok</p><object data="evil"></object><embed src="evil">',
    );
    expect(out).not.toMatch(/<object/i);
    expect(out).not.toMatch(/<embed/i);
    expect(out).not.toMatch(/evil/);
  });

  it("decodes entity-encoded markup before sanitizing", () => {
    // decodeEscapedHtml only fires when the source looks like escaped
    // markup, e.g. when Apify hands us &lt;p&gt;...&lt;/p&gt;.
    expect(normalizeDescriptionHtml("&lt;p&gt;Hello&lt;/p&gt;")).toBe(
      "<p>Hello</p>",
    );
  });

  it("returns null when sanitization strips all content", () => {
    // Only disallowed tags -> sanitizer keeps text only; pure tag soup
    // with no text content sanitizes down to empty.
    expect(normalizeDescriptionHtml("<script></script>")).toBeNull();
    expect(normalizeDescriptionHtml("<iframe></iframe>")).toBeNull();
  });
});
