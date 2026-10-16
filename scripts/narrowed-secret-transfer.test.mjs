import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createSecretTransferHandler, publicKeyDetails } from "../deploy/ai-filter-refresh/secret-transfer.mjs";
import { buildTransferBundle, decryptTransfer } from "./narrowed-secret-transfer.mjs";
import { recoverNarrowedRuntime } from "./recover-narrowed-runtime.mjs";

const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 3072,
  publicKeyEncoding: { type: "spki", format: "pem" }, privateKeyEncoding: { type: "pkcs8", format: "pem" } });
const transferId = "ab".repeat(16);
const now = Date.parse("2026-10-16T12:00:00Z");
const config = { publicKey, transferId, expiresAt: new Date(now + 60_000).toISOString() };
const env = { VERCEL_ENV: "production", AI_FILTER_REFRESH_SECRET: "fixture-bearer-".repeat(4),
  AI_FILTER_CACHE_HMAC_SECRET: "fixture-original-hmac-".repeat(3), TYPESAFE_AI_TOKEN: "fixture-provider-token",
  AI_FILTER_ENABLED: "true", AI_FILTER_JEV_1_13_0_ENABLED: "true", TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT: "fixture-receipt",
  AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS: "12345", OTHER_PRIVATE_CREDENTIAL: "must-never-export" };

async function request(options = {}) {
  const server = createServer(createSecretTransferHandler(config, options.env ?? env, () => options.now ?? now));
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/`, {
      method: options.method ?? "POST", headers: { Authorization: options.authorization ?? `Bearer ${env.AI_FILTER_REFRESH_SECRET}` },
    });
    return { status: response.status, cache: response.headers.get("cache-control"), body: await response.text() };
  } finally { await new Promise(resolve => server.close(resolve)); }
}

test("authenticated production recovery preserves originals and exposes only sealed allowlisted data", async () => {
  const response = await request();
  assert.equal(response.status, 200);
  assert.match(response.cache, /no-store/);
  for (const secret of [env.AI_FILTER_CACHE_HMAC_SECRET, env.TYPESAFE_AI_TOKEN, env.OTHER_PRIVATE_CREDENTIAL]) assert.ok(!response.body.includes(secret));
  const clear = decryptTransfer(JSON.parse(response.body), privateKey, transferId);
  assert.equal(clear.hmac, env.AI_FILTER_CACHE_HMAC_SECRET);
  assert.equal(clear.typesafe, env.TYPESAFE_AI_TOKEN);
  assert.equal(clear.policy.values.AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS, "12345");
  assert.equal(clear.policy.values.STRIPE_ENVIRONMENT, null);
  assert.ok(!JSON.stringify(clear).includes(env.OTHER_PRIVATE_CREDENTIAL));
});

test("authentication, method, expiry and production context fail without disclosing credentials", async () => {
  for (const [options, status] of [
    [{ authorization: "Bearer incorrect" }, 401], [{ method: "GET" }, 405], [{ now: now + 60_000 }, 410],
    [{ env: { ...env, VERCEL_ENV: "preview" } }, 503], [{ env: { ...env, AI_FILTER_ENABLED: "" } }, 503],
  ]) {
    const response = await request(options);
    assert.equal(response.status, status);
    assert.deepEqual(JSON.parse(response.body), { error: "transfer_unavailable" });
  }
});

test("ciphertext, transfer binding and expiry metadata cannot be altered", async () => {
  const packet = JSON.parse((await request()).body);
  assert.throws(() => decryptTransfer(packet, privateKey, "cd".repeat(16)));
  assert.throws(() => decryptTransfer({ ...packet, expiresAt: new Date(now + 120_000).toISOString() }, privateKey, transferId));
  assert.throws(() => decryptTransfer({ ...packet, ciphertext: (packet.ciphertext[0] === "A" ? "B" : "A") + packet.ciphertext.slice(1) }, privateKey, transferId));
  assert.throws(() => decryptTransfer({ ...packet, plaintext: env.AI_FILTER_CACHE_HMAC_SECRET }, privateKey, transferId));
});

test("weak and private keys cannot enter the public deployment artifact", () => {
  assert.throws(() => publicKeyDetails(privateKey));
  const weak = generateKeyPairSync("rsa", { modulusLength: 1024 }).publicKey.export({ type: "spki", format: "pem" });
  assert.throws(() => publicKeyDetails(String(weak)));
});

test("the isolated artifact has no cron, no app routes and no operator secrets", () => {
  const root = mkdtempSync(join(tmpdir(), "narrowed-transfer-test-"));
  try {
    const manifest = buildTransferBundle(root, { publicKey, transferId, revision: "a".repeat(40), projectId: "prj_fixture", orgId: "team_fixture" }, now);
    assert.equal(Date.parse(manifest.expiresAt) - now, 20 * 60_000);
    const output = join(root, ".vercel/output");
    const routes = JSON.parse(readFileSync(join(output, "config.json"), "utf8"));
    assert.deepEqual(routes.crons, []);
    assert.deepEqual(routes.routes, [{ src: "/api/narrowed-secret-transfer", dest: "/transfer" }, { src: "/.*", status: 404 }]);
    assert.equal(JSON.parse(readFileSync(join(output, "builds.json"), "utf8")).target, "production");
    assert.deepEqual(readdirSync(join(root, "apps/web")), []);
    assert.deepEqual(readdirSync(join(output, "functions")), ["transfer.func"]);
    const source = readFileSync(join(output, "functions/transfer.func/handler.mjs"), "utf8");
    assert.ok(!source.includes(privateKey));
    assert.ok(!source.includes(env.AI_FILTER_CACHE_HMAC_SECRET));
    assert.equal(JSON.parse(readFileSync(join(output, "functions/transfer.func/.vc-config.json"), "utf8")).runtime, "nodejs24.x");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("recovery transport never promotes and preserves only sealed artifacts", () => {
  const transport = readFileSync("scripts/recover-narrowed-runtime.mjs", "utf8");
  assert.match(transport, /"--prod", "--skip-domain"/);
  assert.doesNotMatch(transport, /vc\(\["promote"|"--public"/);
  assert.ok(transport.indexOf('const deployed = JSON.parse(vc(["deploy"') < transport.indexOf("writeFileSync(authPath"));
  assert.match(transport, /requireLiveUnchanged/);
  assert.match(transport, /Refusing to delete an aliased deployment/);
  const workflow = readFileSync(".github/workflows/recover-narrowed-runtime.yml", "utf8");
  assert.match(workflow, /environment: production/);
  assert.match(workflow, /test "\$ACTOR" = viktor-shcherb/);
  assert.match(workflow, /test "\$DISPATCH_SHA" = "\$EXPECTED_SHA"/);
  assert.match(workflow, /retention-days: 1/);
  assert.doesNotMatch(workflow, /private_key|AI_FILTER_CACHE_HMAC_SECRET:|TYPESAFE_AI_TOKEN:/);
});

test("transport captures the sealed payload, excludes auth from upload, deletes its deployment and refuses aliases", async () => {
  for (const aliasState of ["none", "stale", "assigned"]) {
    const acquireAlias = aliasState === "assigned";
    const root = mkdtempSync(join(tmpdir(), "narrowed-transport-test-"));
    const stage = join(root, `narrowed-transfer-${transferId}`);
    const project = "prj_NqkWn9aYWWGsrxGjQf69vkR1POAi";
    const org = "team_ISYNRHtVnEHjiNt5tFGpOXjI";
    let deleted = false;
    const commands = [];
    const execute = (command, args) => {
      commands.push([command, ...args]);
      if (command === "git") return args[0] === "rev-parse" ? "a".repeat(40) : "";
      assert.equal(command, "pnpm");
      const action = args[2];
      if (action === "api") {
        const path = args[3];
        if (path.includes("/v4/aliases/")) return JSON.stringify({ deploymentId: path.includes("jobseek-web-") || !acquireAlias ? "dpl_existing" : "dpl_transfer" });
        if (path.includes("/v9/projects/")) return JSON.stringify({ id: project, accountId: org });
        if (path.includes("/deployments/jseek.co")) return JSON.stringify({ id: "dpl_existing" });
        return JSON.stringify({ projectId: project, target: "production", meta: { narrowedTransfer: transferId }, alias: aliasState === "none" ? [] : ["jseek.co"] });
      }
      if (action === "deploy") {
        assert.ok(args.includes("--skip-domain"));
        assert.equal(existsSync(join(stage, "request.private.curl")), false);
        assert.deepEqual(readdirSync(join(stage, "apps/web")), []);
        return JSON.stringify({ status: "ok", deployment: { id: "dpl_transfer", url: "https://transfer.vercel.app" } });
      }
      if (action === "inspect") return "";
      if (action === "curl") {
        assert.equal(statSync(join(stage, "request.private.curl")).mode & 0o777, 0o600);
        const manifest = JSON.parse(readFileSync(join(stage, "transfer-manifest.json"), "utf8"));
        const handler = createSecretTransferHandler({ publicKey, transferId, expiresAt: manifest.expiresAt }, env);
        let packet;
        const response = { statusCode: 0, setHeader() {}, end(value) { packet = value; } };
        handler({ method: "POST", headers: { authorization: `Bearer ${env.AI_FILTER_REFRESH_SECRET}` } }, response);
        assert.equal(response.statusCode, 200);
        writeFileSync(join(stage, "sealed-runtime.json"), packet);
        return "200";
      }
      if (action === "remove") { assert.ok(args.includes("--safe")); deleted = true; return ""; }
      throw new Error("Unexpected transport operation");
    };
    const task = () => recoverNarrowedRuntime({ RUNNER_TEMP: root, VERCEL_PROJECT_ID: project, VERCEL_ORG_ID: org,
      EXPECTED_SHA: "a".repeat(40), TRANSFER_ID: transferId, EXPECTED_PRODUCTION_DEPLOYMENT_ID: "dpl_existing",
      TRANSFER_PUBLIC_KEY_BASE64: Buffer.from(publicKey).toString("base64"), AI_FILTER_REFRESH_SECRET: env.AI_FILTER_REFRESH_SECRET,
      VERCEL_TOKEN: "fixture-vercel-token", GITHUB_OUTPUT: join(root, "outputs") }, {
      execute, request: async () => { assert.equal(deleted, true); return { status: 404, body: { cancel: async () => {} } }; },
    });
    try {
      if (acquireAlias) { await assert.rejects(task); assert.equal(deleted, false); }
      else {
        await task();
        assert.equal(deleted, true);
        const packet = JSON.parse(readFileSync(join(stage, "sealed-runtime.json"), "utf8"));
        assert.equal(decryptTransfer(packet, privateKey, transferId).hmac, env.AI_FILTER_CACHE_HMAC_SECRET);
        assert.match(readFileSync(join(root, "outputs"), "utf8"), /sealed=true/);
      }
      assert.equal(existsSync(join(stage, "request.private.curl")), false);
      assert.ok(!JSON.stringify(commands).includes(env.AI_FILTER_REFRESH_SECRET));
    } finally { rmSync(root, { recursive: true, force: true }); }
  }
});
