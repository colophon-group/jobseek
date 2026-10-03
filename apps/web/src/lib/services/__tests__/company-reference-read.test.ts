import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({ rows: vi.fn(), canonical: vi.fn() }));
vi.mock("@/db", () => ({ db: { execute: mocks.rows } }));
vi.mock("@/lib/services/company-references", () => ({ fetchCanonicalCompanyReferences: mocks.canonical }));
import { readCompanyReferences } from "../company-reference-read";
const retainedId = "11111111-1111-4111-8111-111111111111";
const newId = "22222222-2222-4222-8222-222222222222";
const retiredId = "33333333-3333-4333-8333-333333333333";
const retained = { id: retainedId, name: "Retained", slug: "retained", icon: null };
const canonical = { id: newId, name: "New", slug: "new", icon: null };

describe("readCompanyReferences", () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.rows.mockResolvedValue([retained]); mocks.canonical.mockResolvedValue([canonical]); });
  it("hydrates new selections without writes and retains the requested order", async () => {
    expect(await readCompanyReferences([newId, retainedId, retiredId])).toEqual([
      canonical, retained, { id: retiredId, name: "Company unavailable", slug: "", icon: null, unavailable: true },
    ]);
    expect(mocks.canonical).toHaveBeenCalledWith([newId, retiredId]);
  });
  it("uses retained snapshots offline without a catalogue request", async () => {
    expect(await readCompanyReferences([retainedId])).toEqual([retained]);
    expect(mocks.canonical).not.toHaveBeenCalled();
  });
  it("preserves all missing selection identities on outage", async () => {
    mocks.canonical.mockRejectedValue(new Error("unavailable"));
    const rows = await readCompanyReferences([newId, retainedId]);
    expect(rows.map((row) => row.id)).toEqual([newId, retainedId]);
    expect(rows[0].unavailable).toBe(true);
    expect(rows[1]).toEqual(retained);
  });
  it("rejects invalid identifiers before database or catalogue access", async () => {
    await expect(readCompanyReferences(["invalid"])).rejects.toThrow("invalid_company");
    expect(mocks.rows).not.toHaveBeenCalled();
    expect(mocks.canonical).not.toHaveBeenCalled();
  });
});
