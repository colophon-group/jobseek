import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

function readSearchSource(fileName: string) {
  return readFileSync(join(process.cwd(), "src/components/search", fileName), "utf8");
}

describe("SearchBar source structure (#3082)", () => {
  it("keeps suggestion rendering and fetching split out of the main component", () => {
    const searchBar = readSearchSource("search-bar.tsx");
    const typeahead = readSearchSource("search-bar-typeahead.ts");
    const queryIntent = readSearchSource("use-search-bar-query-intent.ts");

    expect((searchBar.match(/<SearchBarSuggestionSection/g) ?? []).length).toBe(6);
    // Keyword, full-query proposal, correction, and company request are the
    // direct rows; taxonomy groups stay in SearchBarSuggestionSection.
    expect((searchBar.match(/data-suggestion/g) ?? []).length).toBeLessThanOrEqual(5);
    expect(searchBar).not.toContain("suggestCompanies({ query })");
    expect(typeahead).toContain("function useSearchBarTypeahead");
    expect(typeahead).toContain("requestGenerationRef");
    expect(searchBar).toContain("useSearchBarQueryIntent(");
    expect(queryIntent).toContain('fetch("/api/search/query-intent"');
  });
});
