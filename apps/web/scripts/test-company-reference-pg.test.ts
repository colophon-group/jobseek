import { randomUUID } from "node:crypto";
import { AsyncLocalStorage } from "node:async_hooks";
import type { Sql } from "postgres";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { companyDocument, fixtureClient, resetFixture, seedUser } from "./company-reference/fixture";

const runtime = vi.hoisted(() => ({ client: null as Sql | null, user: null as AsyncLocalStorage<string | null> | null,
  companies: new Map<string, Record<string, unknown>>(), requests: 0, outage: false, beforeSearch: null as (() => Promise<void>) | null }));
vi.mock("server-only", () => ({}));
vi.mock("next/server", () => ({ after: () => {} }));
vi.mock("next/cache", () => ({ revalidatePath: () => {}, updateTag: () => {}, cacheLife: () => {}, cacheTag: () => {} }));
vi.mock("@/lib/sessionCache", () => ({ getSessionUserId: async () => runtime.user?.getStore() ?? null }));
vi.mock("@/db", async () => {
  const { drizzle } = await import("drizzle-orm/postgres-js");
  const schema = await import("@/db/schema");
  const { fixtureClient } = await import("./company-reference/fixture");
  runtime.client = fixtureClient();
  return { db: drizzle(runtime.client, { schema }) };
});
vi.mock("@/lib/cache", () => ({ cached: async (_key: string, load: () => unknown) => load(), invalidate: async () => {},
  kvMget: async (keys: unknown[]) => keys.map(() => null), kvGet: async () => null, kvSet: async () => {} }));
vi.mock("@/lib/rate-limit", () => ({ sharedWatchlistCloneLimiter: { limit: async () => ({ success: true }) } }));
// Fixture sits at the external provider boundary: all company validation/materialization and SQL run unchanged.
vi.mock("@/lib/search/typesense-client", () => ({ getSearchClient: () => ({ collections: () => ({ documents: (id?: string) => ({
  retrieve: async () => {
    runtime.requests++;
    if (runtime.outage) throw Object.assign(new Error("fixture unavailable"), { httpStatus: 503 });
    const document = id && runtime.companies.get(id);
    if (!document) throw Object.assign(new Error("fixture absent"), { httpStatus: 404 });
    return document;
  },
  search: async (params: { filter_by?: string }) => {
    runtime.requests++;
    if (runtime.outage) throw Object.assign(new Error("fixture unavailable"), { httpStatus: 503 });
    await runtime.beforeSearch?.();
    const hits = [...runtime.companies.values()].filter((doc) => !params.filter_by || params.filter_by.includes(String(doc.id)) || params.filter_by.includes(String(doc.slug))).map(document => ({ document }));
    return { found: hits.length, hits };
  },
}) }) }), getWriteClient: () => null }));

import * as watchlists from "../src/lib/services/watchlists";
import { toggleStarredCompany, getStarredCompanyIds } from "../src/lib/actions/starred-companies";

let sql: ReturnType<typeof fixtureClient>;
let owner: string;
let stranger: string;
const as = <T>(id: string | null, fn: () => Promise<T>) => runtime.user!.run(id, fn);
const document = () => { const doc = companyDocument(); runtime.companies.set(doc.id, doc); return doc; };
async function create(ids: string[] = []) {
  const result = await as(owner, () => watchlists.createWatchlist({ title: "Fixture watchlist", companyIds: ids, filters: { anyCompany: false } }));
  expect(result).not.toHaveProperty("error");
  if (!("id" in result)) throw new Error("Fixture create failed");
  return result.id;
}
async function membership(id: string) {
  return (await sql`SELECT company_id FROM watchlist_company WHERE watchlist_id=${id} ORDER BY company_id`).map(row => row.company_id);
}
async function absent(id: string) {
  expect(await sql`SELECT id FROM company_reference WHERE id=${id}`).toHaveLength(0);
  expect(await sql`SELECT id FROM company WHERE id=${id}`).toHaveLength(0);
}

beforeAll(() => { runtime.user = new AsyncLocalStorage(); sql = runtime.client!;
  vi.stubEnv("TYPESENSE_HOST", "127.0.0.1"); vi.stubEnv("TYPESENSE_PORT", "1"); vi.stubEnv("TYPESENSE_PROTOCOL", "http"); vi.stubEnv("TYPESENSE_SEARCH_KEY", "fixture");
});
afterAll(async () => { await sql?.end({ timeout: 5 }); });

describe.each(["bridge", "reference"] as const)("company selection persistence against real PostgreSQL in %s mode", writeMode => {
  beforeEach(async () => {
    vi.stubEnv("COMPANY_REFERENCE_WRITE_MODE", writeMode);
    await resetFixture(sql, writeMode); owner = (await seedUser(sql)).id; stranger = (await seedUser(sql)).id;
    runtime.companies.clear(); runtime.requests = 0; runtime.outage = false; runtime.beforeSearch = null;
  });
  it("creates from canonical search without either pre-existing representation and reloads/shares/copies", async () => {
    const doc = document(); await absent(doc.id);
    const id = await create([doc.id]);
    expect(await membership(id)).toEqual([doc.id]);
    expect(await sql`SELECT id FROM company WHERE id=${doc.id}`).toHaveLength(writeMode === "bridge" ? 1 : 0);
    if (writeMode === "reference") {
      await expect(sql`DELETE FROM company_reference WHERE id=${doc.id}`).rejects.toMatchObject({ code: "23001" });
      await sql`INSERT INTO company (id, name, slug) VALUES (${doc.id}, 'Unrelated catalogue mirror', ${doc.slug})`;
      await sql`DELETE FROM company WHERE id=${doc.id}`;
      expect(await membership(id)).toEqual([doc.id]);
    }
    expect((await sql`SELECT name, slug, source, verified_at FROM company_reference WHERE id=${doc.id}`)[0]).toMatchObject({ name: doc.name, slug: doc.slug, source: "typesense" });
    expect((await sql`SELECT verified_at IS NOT NULL AS verified FROM company_reference WHERE id=${doc.id}`)[0].verified).toBe(true);
    const detail = await watchlists.getOwnedWatchlistById(id, owner);
    expect(detail?.companies).toEqual([{ id: doc.id, name: doc.name, slug: doc.slug, icon: null }]);
    expect(await as(owner, () => watchlists.shareWatchlist(id))).toHaveProperty("ok", true);
    expect((await watchlists.getSharedWatchlistById(id))?.companies.map(company => company.id)).toEqual([doc.id]);
    const copy = await as(stranger, () => watchlists.copySharedWatchlist(id));
    expect(copy).toHaveProperty("id");
    if ("id" in copy) expect(await membership(copy.id)).toEqual([doc.id]);
  });

  it("replaces membership, adds one company, stars, and hydrates handoff slugs", async () => {
    const id = await create();
    const replaced = document(); await absent(replaced.id);
    expect(await as(owner, () => watchlists.updateWatchlist({ watchlistId: id, companyIds: [replaced.id] }))).toHaveProperty("slug");
    const added = document(); await absent(added.id);
    expect(await as(owner, () => watchlists.addCompanyToWatchlist(id, added.id))).toMatchObject({ ok: true });
    const starred = document(); await absent(starred.id);
    expect(await as(owner, () => toggleStarredCompany(starred.id))).toMatchObject({ starred: true });
    expect(await as(owner, getStarredCompanyIds)).toEqual([starred.id]);
    const handed = document(); await absent(handed.id);
    const result = await as(owner, () => watchlists.createWatchlistFromHandoff({ title: "Handoff fixture", companySlugs: [handed.slug] }));
    expect(result).toHaveProperty("id");
    if ("id" in result) expect(await membership(result.id)).toEqual([handed.id]);
  });

  it("fails whole replacement for a partially missing batch without changing membership, title, or references", async () => {
    const existing = document(); const id = await create([existing.id]);
    const fresh = document(); const unknown = randomUUID();
    const before = await sql`SELECT title, slug FROM watchlist WHERE id=${id}`;
    expect(await as(owner, () => watchlists.updateWatchlist({ watchlistId: id, title: "Rejected replacement", companyIds: [fresh.id, unknown] }))).toEqual({ error: "unknown_company" });
    expect(await membership(id)).toEqual([existing.id]);
    expect(await sql`SELECT title, slug FROM watchlist WHERE id=${id}`).toEqual(before);
    await absent(fresh.id);
  });

  it("preserves existing selections during outage/retirement and explicitly rejects unknown references", async () => {
    const existing = document(); const id = await create([existing.id]);
    runtime.companies.delete(existing.id); runtime.outage = true;
    expect(await as(owner, () => watchlists.updateWatchlist({ watchlistId: id, companyIds: [existing.id] }))).toHaveProperty("slug");
    expect(await create([existing.id])).toBeTruthy();
    expect((await watchlists.getOwnedWatchlistById(id, owner))?.companies[0].name).toBe(existing.name);
    const unknown = randomUUID();
    expect(await as(owner, () => watchlists.updateWatchlist({ watchlistId: id, companyIds: [unknown] }))).toEqual({ error: "company_lookup_unavailable" });
    expect(await membership(id)).toEqual([existing.id]);
    expect(await as(owner, () => watchlists.removeCompanyFromWatchlist(id, existing.id))).toMatchObject({ ok: true });
    expect(await membership(id)).toEqual([]);
  });

  it("authorizes before provider lookup or materialization, and rejects unauthenticated stars", async () => {
    const id = await create(); const fresh = document();
    runtime.requests = 0;
    expect(await as(stranger, () => watchlists.updateWatchlist({ watchlistId: id, companyIds: [fresh.id] }))).toEqual({ error: "not_found" });
    expect(await as(stranger, () => watchlists.addCompanyToWatchlist(id, fresh.id))).toMatchObject({ ok: false });
    await expect(as(null, () => toggleStarredCompany(fresh.id))).rejects.toThrow("Not authenticated");
    expect(runtime.requests).toBe(0); await absent(fresh.id);
  });

  it("creates the same missing reference concurrently with idempotent SQL insertion", async () => {
    const fresh = document(); await absent(fresh.id);
    const results = await Promise.all([create([fresh.id]), create([fresh.id]), as(stranger, () => toggleStarredCompany(fresh.id))]);
    expect(results[2]).toMatchObject({ starred: true });
    expect(await sql`SELECT id FROM company_reference WHERE id=${fresh.id}`).toHaveLength(1);
    expect(await sql`SELECT id FROM company WHERE id=${fresh.id}`).toHaveLength(writeMode === "bridge" ? 1 : 0);
  });

  if (writeMode === "bridge") {
  it("promotes a legacy seed inserted during provider lookup to the verified canonical snapshot", async () => {
    const fresh = document(); await absent(fresh.id);
    runtime.beforeSearch = async () => {
      runtime.beforeSearch = null;
      await sql`INSERT INTO company (id, name, slug) VALUES (${fresh.id}, 'Legacy race display', ${fresh.slug})`;
    };
    const id = await create([fresh.id]);
    expect(await membership(id)).toEqual([fresh.id]);
    const [reference] = await sql`SELECT name, source, verified_at IS NOT NULL AS verified FROM company_reference WHERE id=${fresh.id}`;
    expect(reference).toMatchObject({ name: fresh.name, source: "typesense" });
    expect(reference.verified).toBe(true);
  });

  }

  it("preserves a canonical snapshot committed by a concurrent first-use request", async () => {
    const fresh = document(); await absent(fresh.id);
    runtime.beforeSearch = async () => {
      runtime.beforeSearch = null;
      await sql`INSERT INTO company_reference (id, name, slug, source, verified_at)
        VALUES (${fresh.id}, 'Already verified concurrently', ${fresh.slug}, 'typesense', now())`;
    };
    await create([fresh.id]);
    expect((await sql`SELECT name, source FROM company_reference WHERE id=${fresh.id}`)[0])
      .toMatchObject({ name: "Already verified concurrently", source: "typesense" });
  });

  it("serializes the final membership slot without persisting the rejected reference", async () => {
    const documents = Array.from({ length: 249 }, () => document());
    const id = await create(documents.map(doc => doc.id));
    const one = document(); const two = document();
    const results = await Promise.all([one, two].map(doc => as(owner, () => watchlists.addCompanyToWatchlist(id, doc.id))));
    expect(results.filter(result => result.ok)).toHaveLength(1);
    expect(await membership(id)).toHaveLength(250);
    expect(await sql`SELECT id FROM company_reference WHERE id IN (${one.id}, ${two.id})`).toHaveLength(1);
  });

  if (writeMode === "bridge") {
  it("shares company-to-reference lock order with an overlapping legacy writer", async () => {
    const fresh = document(); await absent(fresh.id);
    // A second AFTER INSERT trigger pauses the old writer after its company row
    // is locked but before the real production compatibility trigger runs.
    await sql.unsafe(`CREATE FUNCTION pause_fixture_legacy_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      PERFORM pg_advisory_xact_lock(10227001); RETURN NEW; END $$;
      CREATE TRIGGER aaa_pause_fixture_legacy_insert AFTER INSERT ON company FOR EACH ROW EXECUTE FUNCTION pause_fixture_legacy_insert()`);
    let oldWriter: Promise<unknown> | undefined;
    let newWriter: Promise<string> | undefined;
    await sql.begin(async gate => {
      await gate`SELECT pg_advisory_xact_lock(10227001)`;
      oldWriter = Promise.resolve(sql`INSERT INTO company (id, name, slug) VALUES (${fresh.id}, 'Legacy overlap', ${fresh.slug})`);
      const deadline = Date.now() + 5_000;
      let waiting = false;
      while (Date.now() < deadline) {
        const activity = await sql`SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
          AND query ILIKE 'insert into company%' AND wait_event='advisory'`;
        if (activity.length) { waiting = true; break; }
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      expect(waiting).toBe(true);
      newWriter = create([fresh.id]);
      while (runtime.requests === 0) await new Promise(resolve => setTimeout(resolve, 10));
      await new Promise(resolve => setTimeout(resolve, 100));
    });
    await Promise.all([oldWriter, newWriter]);
    expect((await sql`SELECT name, source, verified_at IS NOT NULL AS verified FROM company_reference WHERE id=${fresh.id}`)[0])
      .toMatchObject({ name: fresh.name, source: "typesense", verified: true });
    expect(await membership(await newWriter!)).toEqual([fresh.id]);
  });

  }

  it("keeps the account capacity check atomic without orphaning new references", async () => {
    for (let i = 0; i < 9; i++) await create();
    const one = document(); const two = document();
    const results = await Promise.all([one, two].map(doc => as(owner, () => watchlists.createWatchlist({ title: `Capacity ${doc.id}`, companyIds: [doc.id] }))));
    expect(results.filter(result => "id" in result)).toHaveLength(1);
    expect(results.filter(result => "error" in result)).toEqual([{ error: "limit_reached" }]);
    const persisted = await sql`SELECT id FROM company_reference WHERE id IN (${one.id}, ${two.id})`;
    expect(persisted).toHaveLength(1);
  });

  it("enforces real foreign keys and rolls back reference inserts when membership insertion fails", async () => {
    const fresh = document();
    await sql.unsafe(`CREATE FUNCTION reject_fixture_membership() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture rejection'; END $$;
      CREATE TRIGGER reject_fixture_membership BEFORE INSERT ON watchlist_company FOR EACH ROW EXECUTE FUNCTION reject_fixture_membership()`);
    await expect(as(owner, () => watchlists.createWatchlist({ title: "Rollback", companyIds: [fresh.id] }))).rejects.toThrow();
    expect(await sql`SELECT id FROM watchlist`).toHaveLength(0); await absent(fresh.id);
    const other = randomUUID();
    await expect(sql`INSERT INTO followed_company (user_id, company_id) VALUES (${owner}, ${other})`).rejects.toMatchObject({ code: "23503" });
  });
});
