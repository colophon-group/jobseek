import { execFileSync } from "node:child_process";
import { appendFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { buildTransferBundle } from "./narrowed-secret-transfer.mjs";
import { validateEnvelope } from "../deploy/ai-filter-refresh/secret-transfer.mjs";

const projectId = "prj_NqkWn9aYWWGsrxGjQf69vkR1POAi";
const orgId = "team_ISYNRHtVnEHjiNt5tFGpOXjI";
const scope = "viktor-shcherbakovs-projects";
let recoveryStage = "identity";

export async function recoverNarrowedRuntime(env = process.env, { execute = execFileSync, request = fetch } = {}) {
  recoveryStage = "identity";
  const revision = env.EXPECTED_SHA;
  const transferId = env.TRANSFER_ID;
  const expectedLive = env.EXPECTED_PRODUCTION_DEPLOYMENT_ID;
  if (!env.RUNNER_TEMP || env.VERCEL_PROJECT_ID !== projectId || env.VERCEL_ORG_ID !== orgId ||
    !/^[a-f0-9]{40}$/.test(revision ?? "") || !/^[a-f0-9]{32}$/.test(transferId ?? "") ||
    !/^dpl_[A-Za-z0-9]+$/.test(expectedLive ?? "") || !/^[A-Za-z0-9+/]+={0,2}$/.test(env.TRANSFER_PUBLIC_KEY_BASE64 ?? "")) throw new Error("Invalid recovery identity");
  const git = args => execute("git", args, { encoding: "utf8" }).trim();
  if (git(["rev-parse", "HEAD"]) !== revision || git(["status", "--porcelain", "--untracked-files=all"])) throw new Error("Unreviewed recovery source");
  const root = resolve(env.RUNNER_TEMP, `narrowed-transfer-${transferId}`);
  mkdirSync(root, { mode: 0o700 });
  const vc = (args, cwd = root) => execute("pnpm", ["dlx", "vercel@62.1.0", ...args], {
    cwd, env, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 180_000, maxBuffer: 2 * 1024 * 1024,
  }).trim();
  const api = path => JSON.parse(vc(["api", `${path}${path.includes("?") ? "&" : "?"}teamId=${orgId}`, "--raw"]));
  const canonical = () => api("/v13/deployments/jseek.co");
  const requireLiveUnchanged = () => { if (canonical().id !== expectedLive) throw new Error("Production identity changed during recovery"); };
  const metadata = api(`/v9/projects/${projectId}`);
  if (metadata.id !== projectId || metadata.accountId !== orgId) throw new Error("Recovery project mismatch");
  requireLiveUnchanged();
  const manifest = buildTransferBundle(root, {
    publicKey: Buffer.from(env.TRANSFER_PUBLIC_KEY_BASE64, "base64").toString("utf8"),
    transferId, revision, projectId, orgId,
  });
  let candidate;
  const authPath = resolve(root, "request.private.curl");
  try {
    recoveryStage = "deploy";
    const deployed = JSON.parse(vc(["deploy", "--prebuilt", "--prod", "--skip-domain", "--yes", "--format=json", "--archive=tgz", "--scope", scope,
      "--meta", `narrowedTransfer=${transferId}`, "--meta", `githubCommitSha=${revision}`, "--meta", "githubCommitRef=main",
      "--meta", "githubOrg=colophon-group", "--meta", "githubRepo=jobseek"]));
    candidate = deployed.deployment;
    if (deployed.status !== "ok" || !/^dpl_[A-Za-z0-9]+$/.test(candidate?.id ?? "") || !/^https:\/\/[A-Za-z0-9.-]+\.vercel\.app$/.test(candidate?.url ?? "") || candidate.id === expectedLive) throw new Error("Invalid isolated deployment");
    requireLiveUnchanged();
    recoveryStage = "inspect";
    vc(["inspect", candidate.id, "--wait", "--timeout=2m", "--scope", scope]);
    const detail = api(`/v13/deployments/${candidate.id}`);
    if (detail.projectId !== projectId || detail.target !== "production" || detail.meta?.narrowedTransfer !== transferId || (detail.alias?.length ?? 0) !== 0) throw new Error("Recovery deployment acquired aliases or wrong identity");
    writeFileSync(resolve(root, "transfer-manifest.json"), JSON.stringify({ ...manifest, deploymentId: candidate.id, deploymentUrl: candidate.url }, null, 2) + "\n");
    const bearer = env.AI_FILTER_REFRESH_SECRET;
    if (!bearer || bearer.length < 32 || bearer.length > 1024 || /[\r\n\0]/.test(bearer)) throw new Error("Protected recovery authorization unavailable");
    const sealedPath = resolve(root, "sealed-runtime.json");
    // Created only after deployment, so credentials cannot enter the uploaded artifact.
    writeFileSync(authPath, `silent\nshow-error\nrequest = "POST"\nheader = ${JSON.stringify(`Authorization: Bearer ${bearer}`)}\noutput = ${JSON.stringify(sealedPath)}\nwrite-out = "%{http_code}"\n`, { mode: 0o600, flag: "wx" });
    recoveryStage = "seal";
    const status = vc(["curl", "/api/narrowed-secret-transfer", "--deployment", candidate.url, "--scope", scope, "--", "--config", authPath]);
    if (status !== "200") throw new Error("Runtime transfer was refused");
    const packet = JSON.parse(readFileSync(sealedPath, "utf8"));
    validateEnvelope(packet, transferId, manifest.fingerprint);
    if (packet.expiresAt !== manifest.expiresAt) throw new Error("Transfer expiry differs from the deployed artifact");
    requireLiveUnchanged();
    if (env.GITHUB_OUTPUT) appendFileSync(env.GITHUB_OUTPUT, `sealed=true\nsealed_path=${sealedPath}\nmanifest_path=${resolve(root, "transfer-manifest.json")}\n`);
    console.info(JSON.stringify({ event: "narrowed_runtime_sealed", transferId, productionUnchanged: true }));
  } finally {
    rmSync(authPath, { force: true });
    if (candidate && /^dpl_[A-Za-z0-9]+$/.test(candidate.id ?? "") && candidate.id !== expectedLive) {
      const previousStage = recoveryStage;
      recoveryStage = "cleanup";
      requireLiveUnchanged();
      const detail = api(`/v13/deployments/${candidate.id}`);
      if ((detail.alias?.length ?? 0) !== 0) throw new Error("Refusing to delete an aliased deployment");
      vc(["remove", candidate.id, "--safe", "--yes", "--scope", scope]);
      const removed = await request(`https://api.vercel.com/v13/deployments/${candidate.id}?teamId=${orgId}`, {
        headers: { Authorization: `Bearer ${env.VERCEL_TOKEN}` }, signal: AbortSignal.timeout(15_000),
      });
      if (removed.status !== 404) throw new Error("Recovery deployment removal was not confirmed");
      await removed.body?.cancel();
      requireLiveUnchanged();
      console.info(JSON.stringify({ event: "narrowed_transfer_deployment_removed", transferId, deploymentId: candidate.id }));
      recoveryStage = previousStage;
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  void recoverNarrowedRuntime().catch(() => { console.error(JSON.stringify({ event: "narrowed_runtime_recovery_failed", stage: recoveryStage })); process.exitCode = 1; });
}
