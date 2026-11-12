import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const instance = process.argv[2];
if (instance !== "candidate" && instance !== "production") {
  throw new Error("invalid web origin instance");
}

const config = JSON.parse(
  readFileSync(`/etc/jobseek-web/${instance}.json`, "utf8"),
);
if (
  !config ||
  typeof config !== "object" ||
  !/^\/opt\/jobseek-web\/releases\/[a-f0-9]{40}$/.test(config.release) ||
  !config.env ||
  typeof config.env !== "object" ||
  Array.isArray(config.env)
) {
  throw new Error("invalid web origin configuration");
}

for (const [key, value] of Object.entries(config.env)) {
  if (!/^[A-Z][A-Z0-9_]*$/.test(key) || typeof value !== "string" || value.includes("\0")) {
    throw new Error("invalid web origin environment");
  }
  process.env[key] = value;
}

process.env.NODE_ENV = "production";
process.env.HOSTNAME = "127.0.0.1";
process.env.PORT = instance === "candidate" ? "3001" : "3000";
if (instance === "candidate") process.env.JOBSEEK_WEB_CANDIDATE = "1";
else delete process.env.JOBSEEK_WEB_CANDIDATE;

process.chdir(`${config.release}/apps/web`);
await import(pathToFileURL(`${config.release}/apps/web/server.js`).href);
