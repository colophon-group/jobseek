import { access, readFile } from "node:fs/promises";

const packageJson = JSON.parse(await readFile("package.json", "utf8"));
const serverJson = JSON.parse(await readFile("server.json", "utf8"));

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const binTarget = packageJson.bin?.["jobseek-mcp"];

assert(typeof binTarget === "string", "package.json must define bin.jobseek-mcp");
assert(
  binTarget !== "dist/index.js",
  "bin.jobseek-mcp must target a checked-in launcher, not generated dist/index.js",
);

await access(binTarget);

const files = packageJson.files ?? [];
assert(files.includes("dist"), 'package.json files must include "dist"');
assert(
  files.includes(binTarget),
  `package.json files must include the bin target (${binTarget})`,
);

const launcher = await readFile(binTarget, "utf8");
assert(
  launcher.includes("dist/index.js"),
  "bin launcher must delegate to the built dist/index.js entrypoint",
);

assert(
  serverJson.version === packageJson.version,
  "server.json version must match package.json version",
);
assert(
  serverJson.packages?.[0]?.version === packageJson.version,
  "server.json package version must match package.json version",
);
