import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

const [path, command, ...args] = process.argv.slice(2);
if (!path || !command) throw new Error("usage: with-env.mjs CONFIG COMMAND [ARGS...]");
const config = JSON.parse(readFileSync(path, "utf8"));
if (!config?.env || typeof config.env !== "object" || Array.isArray(config.env)) {
  throw new Error("invalid web origin runtime configuration");
}
const result = spawnSync(command, args, {
  env: { ...process.env, ...config.env },
  stdio: "inherit",
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
