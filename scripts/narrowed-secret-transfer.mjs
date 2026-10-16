import { constants, createDecipheriv, createHash, createPrivateKey, createPublicKey, privateDecrypt } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { publicKeyDetails, transferAad, validateEnvelope } from "../deploy/ai-filter-refresh/secret-transfer.mjs";

export function buildTransferBundle(root, { publicKey, transferId, revision, projectId, orgId }, now = Date.now()) {
  const { fingerprint } = publicKeyDetails(publicKey);
  if (!/^[a-f0-9]{32}$/.test(transferId) || !/^[a-f0-9]{40}$/.test(revision) || !/^prj_[A-Za-z0-9]+$/.test(projectId) || !/^team_[A-Za-z0-9]+$/.test(orgId)) throw new Error("Invalid transfer identity");
  const expiresAt = new Date(now + 20 * 60_000).toISOString();
  const functionRoot = resolve(root, ".vercel/output/functions/transfer.func");
  mkdirSync(functionRoot, { recursive: true });
  writeFileSync(resolve(root, ".vercel/project.json"), JSON.stringify({ projectId, orgId }));
  writeFileSync(resolve(root, ".vercel/output/config.json"), JSON.stringify({ version: 3, crons: [], routes: [{ src: "/api/narrowed-secret-transfer", dest: "/transfer" }, { src: "/.*", status: 404 }] }));
  writeFileSync(resolve(root, ".vercel/output/builds.json"), JSON.stringify({ target: "production" }));
  writeFileSync(resolve(functionRoot, ".vc-config.json"), JSON.stringify({ runtime: "nodejs24.x", handler: "handler.mjs", launcherType: "Nodejs", maxDuration: 10, shouldAddHelpers: false }));
  const runtime = fileURLToPath(new URL("../deploy/ai-filter-refresh/secret-transfer.mjs", import.meta.url));
  copyFileSync(runtime, resolve(functionRoot, "secret-transfer.mjs"));
  writeFileSync(resolve(functionRoot, "handler.mjs"), `import { createSecretTransferHandler } from './secret-transfer.mjs';\nexport default createSecretTransferHandler(${JSON.stringify({ publicKey, transferId, expiresAt })});\n`);
  const manifest = { version: 1, transferId, revision, fingerprint, expiresAt, runtimeSha256: createHash("sha256").update(readFileSync(runtime)).digest("hex") };
  writeFileSync(resolve(root, "transfer-manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  return manifest;
}

export function decryptTransfer(packet, privatePem, transferId) {
  const privateKey = createPrivateKey(privatePem);
  const pem = createPublicKey(privateKey).export({ type: "spki", format: "pem" });
  const { fingerprint } = publicKeyDetails(String(pem));
  validateEnvelope(packet, transferId, fingerprint);
  const aes = privateDecrypt({ key: privateKey, padding: constants.RSA_PKCS1_OAEP_PADDING, oaepHash: "sha256" }, Buffer.from(packet.wrappedKey, "base64"));
  let clear;
  try {
    const decipher = createDecipheriv("aes-256-gcm", aes, Buffer.from(packet.iv, "base64"));
    decipher.setAAD(transferAad(packet));
    decipher.setAuthTag(Buffer.from(packet.tag, "base64"));
    clear = Buffer.concat([decipher.update(Buffer.from(packet.ciphertext, "base64")), decipher.final()]);
    if (clear.length > 32768) throw new Error("Invalid transfer size");
    const result = JSON.parse(clear.toString("utf8"));
    if (result.version !== 1 || Object.keys(result).sort().join() !== "hmac,policy,transferId,typesafe,version" || result.transferId !== transferId || typeof result.hmac !== "string" || Buffer.byteLength(result.hmac) < 32 || typeof result.typesafe !== "string" || !result.typesafe || !result.policy ||
      createHash("sha256").update(result.hmac).digest("hex") !== result.policy.hmacSha256 ||
      createHash("sha256").update(result.typesafe).digest("hex") !== result.policy.typesafeSha256) throw new Error("Invalid transferred configuration");
    return result;
  } finally { aes.fill(0); clear?.fill(0); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    if (process.argv[2] !== "decrypt" || process.argv.length !== 7) throw new Error("Invalid arguments");
    const [packetPath, privatePath, transferId, output] = process.argv.slice(3);
    const result = decryptTransfer(JSON.parse(readFileSync(packetPath, "utf8")), readFileSync(privatePath), transferId);
    writeFileSync(output, JSON.stringify(result), { mode: 0o600, flag: "wx" });
    console.info(JSON.stringify({ event: "narrowed_runtime_decrypted", originalSecretPreserved: true }));
  } catch { console.error("Narrowed transfer decryption failed"); process.exitCode = 1; }
}
