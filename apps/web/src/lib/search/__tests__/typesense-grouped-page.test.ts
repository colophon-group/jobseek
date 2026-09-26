import { describe, expect, it } from "vitest";
import { groupedPageRequest, readGroupedPage } from "../typesense-grouped-page";

describe("grouped page boundaries", () => {
  it("uses a final probe at the engine's 250-group cap", () => {
    expect(groupedPageRequest(250, 250)).toEqual({ offset: 250, limit: 250 });
    expect(readGroupedPage({ found: 499, grouped_hits: Array(250).fill({}) }, 250, 250).nextOffset).toBe(500);
    expect(readGroupedPage({ found: 501, grouped_hits: [] }, 500, 250).nextOffset).toBeNull();
  });

  it.each([[-1, 10], [0, 0], [0, 251], [1.5, 10]])(
    "rejects unsupported page offset=%s limit=%s", (offset, limit) => {
      expect(() => groupedPageRequest(offset, limit)).toThrow();
    },
  );

  it.each([undefined, -1, 1.5, Number.NaN])("does not expose an invalid exact posting count: %s", (foundDocs) => {
    expect(readGroupedPage({ found: 1, found_docs: foundDocs }, 0, 10)).not.toHaveProperty("totalPostings");
  });
});
