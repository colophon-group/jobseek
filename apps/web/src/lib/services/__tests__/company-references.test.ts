import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({
  existing: [] as Array<Record<string, unknown>>,
  search: vi.fn(),
  referenceTable: { id: "reference.id", source: "reference.source" },
  companyTable: { id: "company.id" },
}));
vi.mock("@/db/schema", () => ({
  companyReference: mocks.referenceTable,
  company: mocks.companyTable,
}));
vi.mock("@/db", () => ({ db: {
  select: () => ({ from: () => ({ where: async () => mocks.existing }) }),
} }));
vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({ collections: () => ({ documents: () => ({ search: mocks.search }) }) }),
}));
vi.mock("@/lib/search/typesense-retry", async () => ({
  ...await vi.importActual<typeof import("@/lib/search/typesense-retry")>("@/lib/search/typesense-retry"),
  withTypesenseRetry: (callback: () => Promise<unknown>) => callback(),
}));

import {
  fetchCanonicalCompanyReferences,
  prepareCompanyReferences,
  persistCompanyReferences,
  type PreparedCompanyReference,
} from "@/lib/services/company-references";

const ID = "20000000-0000-4000-8000-000000000001";
const OTHER_ID = "20000000-0000-4000-8000-000000000002";
const document = { id: ID, name: "New company", slug: "new-company", icon: null };

beforeEach(() => {
  mocks.existing = [];
  mocks.search.mockReset();
  vi.unstubAllEnvs();
});

describe("company reference identity preparation", () => {
  it("uses retained references without consulting search", async () => {
    mocks.existing = [{ ...document, source: "legacy_seed", verifiedAt: null }];
    expect(await prepareCompanyReferences([ID, ID])).toEqual(mocks.existing);
    expect(mocks.search).not.toHaveBeenCalled();
  });

  it("resolves a first-use canonical UUID with only bounded display fields", async () => {
    mocks.search.mockResolvedValue({ found: 1, hits: [{ document: { ...document, name: " New company ", industry_id: 99 } }] });
    const [prepared] = await prepareCompanyReferences([ID]);
    expect(prepared).toEqual({ ...document, source: "typesense", verifiedAt: expect.any(Date) });
    expect(mocks.search).toHaveBeenCalledWith({ q: "*", filter_by: `id:=[${ID}]`, per_page: 1, include_fields: "id,name,slug,icon" });
  });

  it("allows absent canonical reads but rejects a partial selection batch", async () => {
    mocks.search.mockResolvedValue({ found: 1, hits: [{ document }] });
    expect(await fetchCanonicalCompanyReferences([ID, OTHER_ID])).toHaveLength(1);
    await expect(prepareCompanyReferences([ID, OTHER_ID])).rejects.toMatchObject({ code: "unknown_company" });
  });

  it.each([
    { found: 1, hits: [{ document: { ...document, id: OTHER_ID } }] },
    { found: 2, hits: [{ document }, { document }] },
  ])("rejects foreign or repeated UUID documents", async (response) => {
    mocks.search.mockResolvedValue(response);
    await expect(prepareCompanyReferences([ID])).rejects.toMatchObject({ code: "company_identity_conflict" });
  });

  it.each([
    { found: 2, hits: [{ document }] },
    { found: 1 },
    { found: 1, hits: [{ document: { ...document, name: " " } }] },
    { found: 1, hits: [{ document: { ...document, slug: "unsafe,slug" } }] },
    { found: 1, hits: [{ document: { ...document, name: "x".repeat(301) } }] },
    { found: 1, hits: [{ document: { ...document, name: "bad\u0000name" } }] },
    { found: 1, hits: [{ document: { ...document, icon: "bad\u0007icon" } }] },
    { found: 1, hits: [{ document: { ...document, icon: "x".repeat(2049) } }] },
  ])("classifies malformed responses without accepting incomplete metadata", async (response) => {
    mocks.search.mockResolvedValue(response);
    await expect(prepareCompanyReferences([ID])).rejects.toMatchObject({ code: "company_lookup_unavailable" });
  });

  it("does not expose provider errors", async () => {
    mocks.search.mockRejectedValue(new Error("secret provider response body"));
    await expect(prepareCompanyReferences([ID])).rejects.toThrow("company_lookup_unavailable");
  });

  it("rejects unsafe UUIDs and oversized batches before touching search", async () => {
    await expect(prepareCompanyReferences(["id],other:*"])).rejects.toMatchObject({ code: "invalid_company" });
    await expect(prepareCompanyReferences(Array(251).fill(ID))).rejects.toMatchObject({ code: "invalid_company" });
    expect(mocks.search).not.toHaveBeenCalled();
  });
});

describe("reference-only persistence", () => {
  const prepared: PreparedCompanyReference = { ...document, source: "typesense", verifiedAt: new Date() };
  function transaction() {
    const writes: Array<{ table: unknown; value: unknown; conflict: string; options: unknown }> = [];
    const insert = (table: unknown) => ({ values: (value: unknown) => ({
      onConflictDoNothing: async (options: unknown) => { writes.push({ table, value, conflict: "nothing", options }); },
      onConflictDoUpdate: async (options: unknown) => { writes.push({ table, value, conflict: "update", options }); },
    }) });
    return { writes, tx: { insert } as unknown as Parameters<typeof persistCompanyReferences>[0] };
  }

  it.each([undefined, "bridge", "reference", "invalid"])("never creates a legacy row with stale write-mode setting %s", async (mode) => {
    vi.stubEnv("COMPANY_REFERENCE_WRITE_MODE", mode);
    const { tx, writes } = transaction();
    await persistCompanyReferences(tx, [prepared]);
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({ table: mocks.referenceTable, value: prepared, conflict: "update" });
  });

  it("sorts overlapping batches by canonical UUID before inserting", async () => {
    const other = { ...prepared, id: OTHER_ID };
    const { tx, writes } = transaction();
    await persistCompanyReferences(tx, [other, prepared]);
    expect(writes.map(write => write.value)).toEqual([prepared, other]);
    expect(writes.every(write => write.table === mocks.referenceTable)).toBe(true);
  });

  it("preserves legacy provenance for offline reuse without promoting it", async () => {
    const seed: PreparedCompanyReference = { ...prepared, source: "legacy_seed", verifiedAt: null };
    const { tx, writes } = transaction();
    await persistCompanyReferences(tx, [seed]);
    expect(writes).toEqual([{ table: mocks.referenceTable, value: seed, conflict: "nothing", options: { target: mocks.referenceTable.id } }]);
  });
});
