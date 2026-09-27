import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const search = vi.hoisted(() => vi.fn());
vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({ collections: () => ({ documents: () => ({ search }) }) }),
}));
import { assertAiFilterMiningAllowed } from "./mining-policy";

const id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
describe("mining policy recheck", () => {
  beforeEach(() => search.mockReset());
  it("refuses a stored copy that now has a reservation", async () => {
    search.mockResolvedValue({ found: 1, hits: [{ document: { id, tdm_reserved: true } }] });
    await expect(assertAiFilterMiningAllowed([id])).rejects.toMatchObject({ code: "tdm_reserved" });
  });
  it("rechecks every call without caching an earlier allowed result", async () => {
    search.mockResolvedValueOnce({ found: 1, hits: [{ document: { id, tdm_reserved: false } }] });
    await assertAiFilterMiningAllowed([id]);
    search.mockResolvedValueOnce({ found: 1, hits: [{ document: { id, tdm_reserved: true } }] });
    await expect(assertAiFilterMiningAllowed([id])).rejects.toMatchObject({ code: "tdm_reserved" });
    expect(search).toHaveBeenCalledTimes(2);
  });
  it.each([undefined, { found: 0, hits: [] }, { found: 1, hits: [{ document: { id, tdm_reserved: "false" } }] }])(
    "fails closed on unavailable or malformed eligibility %j", async (response) => {
      search.mockResolvedValue(response);
      await expect(assertAiFilterMiningAllowed([id])).rejects.toMatchObject({ code: "tdm_policy_unavailable" });
    },
  );
});
