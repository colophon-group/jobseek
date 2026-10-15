/** Real local Next/auth/Postgres/Typesense HTTP boundaries; synthetic decisions only.
 * Never loads .env.local, production credentials or invokes the classifier.
 * COMPANY_REFERENCE_TEST_DATABASE_URL must name a disposable loopback *_fixture.
 */
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { once } from "node:events";
import { writeFile } from "node:fs/promises";
import { chromium, type Page } from "playwright";
import { hashPassword } from "better-auth/crypto";
import { fixtureClient, fixtureDatabaseUrl, resetFixture, seedUser, companyDocument } from "./company-reference/fixture";
import { startTypesenseFixture } from "./company-reference/typesense-fixture";
import { logExternalError } from "../src/lib/safe-external-error";

async function main() {
  const databaseUrl = fixtureDatabaseUrl();
  const sql = fixtureClient();
  const probe = createServer().listen(0, "127.0.0.1");
  await once(probe, "listening");
  const port = (probe.address() as { port: number }).port;
  await new Promise<void>(resolve => probe.close(() => resolve()));
  const baseUrl = `http://127.0.0.1:${port}`;
  const company = companyDocument();
  const posts = Array.from({ length: 60 }, (_, index) => ({
    id: randomUUID(), title: `Narrowed fixture role ${index + 1}`, is_active: true, has_content: true,
    first_seen_at: Math.floor(Date.now() / 1000) - index - 10, source_url: "https://example.invalid/job",
    company_id: company.id, company_name: company.name, company_slug: company.slug,
    location_names: ["United States"], location_types: ["remote"], locales: ["en"],
  }));
  const search = await startTypesenseFixture([company], {}, posts);
  const browser = await chromium.launch({ headless: true });
  let child: ChildProcess | undefined;
  let page: Page | undefined;
  let logs = "";
  try {
    await resetFixture(sql, "reference");
    const user = await seedUser(sql);
    const password = `Fixture-${randomUUID()}-Aa1!`;
    const hash = await hashPassword(password);
    await sql`INSERT INTO account (id,account_id,provider_id,user_id,password,updated_at)
      VALUES (${randomUUID()},${user.id},'credential',${user.id},${hash},now())`;
    await sql`INSERT INTO subscription (user_id,plan,status,starts_at) VALUES (${user.id},'unlimited','active',now())`;
    await sql`INSERT INTO user_preferences (user_id,locale,job_languages,notifications_paused) VALUES (${user.id},'en',ARRAY['*'],true)`;
    const id = randomUUID(), configuration = randomUUID(), query = randomUUID(), segment = randomUUID();
    await sql`INSERT INTO watchlist (id,user_id,slug,title,filters) VALUES (${id},${user.id},'interactive-fixture','Interactive fixture','{"anyCompany":true,"workMode":["remote"]}')`;
    await sql`INSERT INTO ai_filter_configuration (id,watchlist_id,owner_id) VALUES (${configuration},${id},${user.id})`;
    const fingerprint = createHash("sha256").update('{"candidateLanguages":[],"companyIds":[],"filters":{"anyCompany":true,"workMode":["remote"]}}').digest("hex");
    await sql`INSERT INTO ai_filter_query_version (id,configuration_id,revision,query_text,normalized_query,model,prompt_version,schema_version,normalizer_version,filter_fingerprint,candidate_languages,horizon_started_at,horizon_ends_at)
      VALUES (${query},${configuration},1,'Remote B2B roles','Remote B2B roles','jev-1.13.0','jev-job-fit-choice-v2','classifier-input-v1','classifier-input-normalizer-v4',${fingerprint},'[]','2000-01-01',now())`;
    await sql`INSERT INTO ai_filter_worker (id) VALUES (1)`;
    await sql`INSERT INTO ai_filter_work (query_version_id,kind,watchlist_id,owner_id,target_offset,accepted_target)
      VALUES (${query},'historical',${id},${user.id},500,20)`;
    await sql`INSERT INTO ai_filter_segment (id,watchlist_id,owner_id,query_version_id,status,window_start,window_end,idempotency_key)
      VALUES (${segment},${id},${user.id},${query},'completed','2000-01-01',now(),${randomUUID()})`;
    async function publish(index: number) {
      const key = createHash("sha256").update(posts[index].id).digest("hex");
      await sql`INSERT INTO ai_filter_global_cache (cache_key,key_version,content_identity,status,decision,model,prompt_version,schema_version,normalizer_version,expires_at)
        VALUES (${key},'ai-filter-cache-hmac-v1',${key},'ready','accepted','jev-1.13.0','jev-job-fit-choice-v2','classifier-input-v1','classifier-input-normalizer-v4',now()+interval '1 day')`;
      await sql`INSERT INTO ai_filter_decision (watchlist_id,owner_id,query_version_id,segment_id,candidate_id,content_identity,cache_key,model_decision,posting_first_seen_at,expires_at)
        VALUES (${id},${user.id},${query},${segment},${posts[index].id},${key},${key},'accepted',to_timestamp(${posts[index].first_seen_at}),now()+interval '1 day')`;
    }
    await publish(0);
    const inherited = Object.fromEntries(Object.entries(process.env).filter(([key]) => ["PATH", "HOME", "TMPDIR", "TEMP", "SystemRoot"].includes(key)));
    child = spawn("pnpm", ["exec", "next", "dev", "--hostname", "127.0.0.1", "--port", String(port)], {
      detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"],
      env: { ...inherited, NODE_ENV: "development", NEXT_TELEMETRY_DISABLED: "1", DATABASE_URL: databaseUrl,
        BETTER_AUTH_URL: baseUrl, BETTER_AUTH_SECRET: "local-narrowed-fixture-secret-never-for-production", TRUSTED_ORIGINS: baseUrl,
        TYPESENSE_HOST: "127.0.0.1", TYPESENSE_PORT: String(search.port), TYPESENSE_PROTOCOL: "http", TYPESENSE_SEARCH_KEY: "fixture-read-key",
        TYPESENSE_BROWSER_PARENT_KEY: "fixture-read-key", AI_FILTER_ENABLED: "true", AI_FILTER_JEV_1_13_0_ENABLED: "true",
        TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT: Buffer.from(JSON.stringify({ authoritativeCount: 60, benchmarkSha256: "a".repeat(64), completedAt: new Date().toISOString(), keyVersion: "uuid-int64-active-v1", partitions: 256, reconciliationRunId: randomUUID(), schemaVersion: "typesense-stable-candidate-order-readiness-v1", unresolved: 0 })).toString("base64url"),
        UPSTASH_REDIS_REST_URL: `http://127.0.0.1:${search.port}/redis`, UPSTASH_REDIS_REST_TOKEN: "fixture-only" },
    });
    const append = (chunk: Buffer) => { logs = (logs + chunk.toString()).slice(-4000); };
    child.stdout?.on("data", append); child.stderr?.on("data", append);
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      if (child.exitCode !== null) throw new Error("Local app exited before readiness");
      try { if ((await fetch(`${baseUrl}/api/auth/get-session`)).status < 500) break; } catch { /* boot */ }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    const signedIn = await context.request.post(`${baseUrl}/api/auth/sign-in/email`, { data: { email: user.email, password }, headers: { origin: baseUrl } });
    assert.equal(signedIn.status(), 200);
    page = await context.newPage();
    page.setDefaultTimeout(45_000);
    const failedRequests: string[] = [];
    page.on("response", response => { if (response.status() >= 400 && new URL(response.url()).pathname.includes("/ai-filter/")) failedRequests.push(`${response.status()} ${new URL(response.url()).pathname}`); });
    await page.goto(`${baseUrl}/en/watchlists/${id}`);
    const narrowed = page.getByRole("region", { name: "Narrowed results", exact: true });
    await narrowed.getByText("Matching results", { exact: true }).waitFor();
    await narrowed.getByText(posts[0].title, { exact: true }).waitFor();
    await narrowed.getByText("1 match · 1 of 60 jobs reviewed", { exact: true }).waitFor();
    assert.doesNotMatch(await page.locator("body").innerText(), /in the last year/);
    const nextRead = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/ai-filter/decisions") && response.status() === 200);
    const started = Date.now();
    await publish(1);
    await nextRead;
    await narrowed.getByText(posts[1].title, { exact: true }).waitFor();
    const deliveryMs = Date.now() - started;
    await narrowed.getByText("2 matches · 2 of 60 jobs reviewed", { exact: true }).waitFor();
    assert.deepEqual(failedRequests, []);
    await page.screenshot({ path: "/tmp/jobseek-narrowed-first-page-ui.png", fullPage: true });
    console.log(JSON.stringify({ cachedMatchVisible: true, newMatchDeliveryMs: deliveryMs, broadTotalsHidden: true, reviewProgress: "2 of 60", screenshot: "/tmp/jobseek-narrowed-first-page-ui.png" }));
  } catch (error) {
    if (page) {
      await page.screenshot({ path: "/tmp/jobseek-narrowed-first-page-ui-failure.png", fullPage: true });
      await writeFile("/tmp/jobseek-narrowed-first-page-ui-failure.txt", await page.locator("body").innerText());
      const failedId = page.url().split("/").pop();
      if (failedId && /^[0-9a-f-]{36}$/.test(failedId)) {
        const state = await page.request.get(`${baseUrl}/api/web/watchlists/${failedId}/ai-filter`);
        console.log(JSON.stringify({ fixtureStatus: state.status(), fixtureState: await state.json() }));
      }
    }
    console.error(logs.replace(/fixture-auth[^\s]*/g, "[fixture]"));
    throw error;
  } finally {
    await browser.close();
    if (child?.pid && child.exitCode === null) {
      if (process.platform === "win32") child.kill("SIGTERM"); else process.kill(-child.pid, "SIGTERM");
    }
    await new Promise<void>(resolve => search.server.close(() => resolve()));
    await sql.end();
  }
}
main().catch(error => { logExternalError("error", { service: "external_http", operation: "narrowed_browser_fixture" }, error); process.exitCode = 1; });
