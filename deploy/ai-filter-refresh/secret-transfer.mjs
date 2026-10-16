import { constants, createCipheriv, createHash, createPublicKey, publicEncrypt, randomBytes, timingSafeEqual } from "node:crypto";

export const POLICY_KEYS = Object.freeze([
  "AI_FILTER_ENABLED", "AI_FILTER_JEV_1_13_0_ENABLED", "TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT",
  "AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS", "AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS",
  "AI_FILTER_MAX_SEGMENTS_PER_USER", "AI_FILTER_MAX_SEGMENTS_PROJECT", "STRIPE_ENVIRONMENT", "PADDLE_ENVIRONMENT",
]);
export const ENVELOPE_KEYS = Object.freeze(["version", "transferId", "expiresAt", "fingerprint", "wrappedKey", "iv", "tag", "ciphertext"]);

export function publicKeyDetails(pem) {
  if (typeof pem !== "string" || pem.length > 4096 || !pem.startsWith("-----BEGIN PUBLIC KEY-----")) throw new Error("Invalid transfer public key");
  const key = createPublicKey(pem);
  if (key.asymmetricKeyType !== "rsa" || key.asymmetricKeyDetails.modulusLength < 3072 || key.asymmetricKeyDetails.modulusLength > 4096) throw new Error("Invalid transfer public key");
  return { key, fingerprint: createHash("sha256").update(key.export({ type: "spki", format: "der" })).digest("hex") };
}

export function transferAad(packet) {
  return Buffer.from(JSON.stringify(["jobseek-narrowed-transfer-v1", packet.transferId, packet.expiresAt, packet.fingerprint]));
}

export function validateEnvelope(packet, transferId, fingerprint) {
  if (!packet || typeof packet !== "object" || Array.isArray(packet) ||
    Object.keys(packet).sort().join() !== [...ENVELOPE_KEYS].sort().join() || packet.version !== 1 ||
    packet.transferId !== transferId || packet.fingerprint !== fingerprint ||
    !Number.isFinite(Date.parse(packet.expiresAt))) throw new Error("Invalid sealed transfer");
  for (const key of ["wrappedKey", "iv", "tag", "ciphertext"]) {
    if (typeof packet[key] !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(packet[key]) || packet[key].length > 65536) throw new Error("Invalid sealed transfer");
  }
  if (Buffer.from(packet.iv, "base64").length !== 12 || Buffer.from(packet.tag, "base64").length !== 16) throw new Error("Invalid sealed transfer");
  return packet;
}

export function createSecretTransferHandler(config, env = process.env, now = Date.now) {
  const { key, fingerprint } = publicKeyDetails(config.publicKey);
  if (!/^[a-f0-9]{32}$/.test(config.transferId) || !Number.isFinite(Date.parse(config.expiresAt))) throw new Error("Invalid transfer configuration");
  return function handler(req, res) {
    res.setHeader("Cache-Control", "private, no-store");
    res.setHeader("Content-Type", "application/json");
    res.setHeader("X-Content-Type-Options", "nosniff");
    const fail = (status) => { res.statusCode = status; res.end(JSON.stringify({ error: "transfer_unavailable" })); };
    if (req.method !== "POST") return fail(405);
    if (now() >= Date.parse(config.expiresAt)) return fail(410);
    const supplied = req.headers.authorization;
    const bearer = env.AI_FILTER_REFRESH_SECRET;
    if (typeof supplied !== "string" || supplied.length > 2048 || !bearer || bearer.length < 32 || bearer.length > 1024) return fail(401);
    const expected = Buffer.from(`Bearer ${bearer}`);
    const actual = Buffer.from(supplied);
    if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) return fail(401);
    let data; let aes;
    try {
      if (env.VERCEL_ENV !== "production" || Buffer.byteLength(env.AI_FILTER_CACHE_HMAC_SECRET ?? "") < 32 || !env.TYPESAFE_AI_TOKEN) throw new Error("Unavailable runtime");
      const values = Object.fromEntries(POLICY_KEYS.map(name => [name, env[name] || null]));
      if (!["true", "false"].includes(values.AI_FILTER_ENABLED) || !["true", "false"].includes(values.AI_FILTER_JEV_1_13_0_ENABLED) || !values.TYPESENSE_STABLE_CANDIDATE_ORDER_RECEIPT) throw new Error("Unavailable policy");
      const digest = value => createHash("sha256").update(value).digest("hex");
      data = Buffer.from(JSON.stringify({
        version: 1, transferId: config.transferId,
        hmac: env.AI_FILTER_CACHE_HMAC_SECRET, typesafe: env.TYPESAFE_AI_TOKEN,
        policy: { version: 1, values, hmacSha256: digest(env.AI_FILTER_CACHE_HMAC_SECRET), typesafeSha256: digest(env.TYPESAFE_AI_TOKEN) },
      }));
      if (data.length > 32768) throw new Error("Unavailable configuration");
      aes = randomBytes(32);
      const packet = { version: 1, transferId: config.transferId, expiresAt: config.expiresAt, fingerprint };
      const iv = randomBytes(12);
      const cipher = createCipheriv("aes-256-gcm", aes, iv);
      cipher.setAAD(transferAad(packet));
      const ciphertext = Buffer.concat([cipher.update(data), cipher.final()]);
      const wrappedKey = publicEncrypt({ key, padding: constants.RSA_PKCS1_OAEP_PADDING, oaepHash: "sha256" }, aes);
      res.statusCode = 200;
      res.end(JSON.stringify({ ...packet, wrappedKey: wrappedKey.toString("base64"), iv: iv.toString("base64"), tag: cipher.getAuthTag().toString("base64"), ciphertext: ciphertext.toString("base64") }));
    } catch { fail(503); }
    finally { data?.fill(0); aes?.fill(0); }
  };
}
