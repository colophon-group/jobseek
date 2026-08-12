import { describe, expect, it } from "vitest";

import { inspectVisibleExploreHtml } from "../../../script/visible-explore-html";

describe("inspectVisibleExploreHtml", () => {
  it("ignores markers and text embedded in executable or style nodes", () => {
    const inspection = inspectVisibleExploreHtml(`<!doctype html>
      <html>
        <head>
          <style>.fake { content: "Explore Jobs data-search-result-company"; }</style>
        </head>
        <body>
          <script type="application/json">
            {"html":"<section data-explore-static-results><article data-search-result-company='fake'>Explore Jobs</article></section>"}
          </script>
          <section data-explore-static-results>
            <h1>Explore Jobs</h1>
            <article data-search-result-company="real">Real company</article>
          </section>
        </body>
      </html>`);

    expect(inspection.staticTextContent).toContain("Explore Jobs");
    expect(inspection.staticTextContent).not.toContain("fake");
    expect(inspection.staticResultsCount).toBe(1);
    expect(inspection.companyResultCount).toBe(1);
  });

  it("ignores inert templates and markers outside the static-results subtree", () => {
    const inspection = inspectVisibleExploreHtml(`
      <template>
        <section data-explore-static-results>
          <article data-search-result-company="template">Template company</article>
        </section>
      </template>
      <section data-explore-interactive hidden>
        <article data-search-result-company="interactive">Interactive company</article>
        <article data-posting-id="hidden-posting">Hidden posting</article>
      </section>
      <section data-explore-static-results data-explore-repository-fallback>
        <article data-search-result-company="visible">Visible company</article>
      </section>`);

    expect(inspection.staticTextContent).toContain("Visible company");
    expect(inspection.staticTextContent).not.toContain("Template company");
    expect(inspection.staticTextContent).not.toContain("Interactive company");
    expect(inspection.staticResultsCount).toBe(1);
    expect(inspection.companyResultCount).toBe(1);
    expect(inspection.repositoryFallbackCount).toBe(1);
    expect(inspection.postingResultCount).toBe(0);
  });

  it("handles mixed-case tags and malformed unclosed raw-text elements structurally", () => {
    const mixedCase = inspectVisibleExploreHtml(`
      <ScRiPt><article data-search-result-company="script">bad</article></sCrIpT>
      <StYlE><article data-search-result-company="style">bad</article></sTyLe>
      <section data-explore-static-results>
        <article data-search-result-company="visible">Visible</article>
      </section>`);
    const unclosedStyle = inspectVisibleExploreHtml(`
      <section data-explore-static-results></section>
      <style><article data-search-result-company="hidden">Hidden`);

    expect(mixedCase.companyResultCount).toBe(1);
    expect(mixedCase.staticTextContent).toContain("Visible");
    expect(mixedCase.staticTextContent).not.toContain("bad");
    expect(unclosedStyle.staticResultsCount).toBe(1);
    expect(unclosedStyle.companyResultCount).toBe(0);
    expect(unclosedStyle.staticTextContent).not.toContain("Hidden");
  });

  it("does not confuse similarly named visible elements with script or style tags", () => {
    const inspection = inspectVisibleExploreHtml(`
      <scripture data-explore-static-results>
        <stylesheet data-search-result-company="visible">Visible company</stylesheet>
      </scripture>`);

    expect(inspection.staticResultsCount).toBe(1);
    expect(inspection.companyResultCount).toBe(1);
    expect(inspection.staticTextContent).toContain("Visible company");
  });
});
