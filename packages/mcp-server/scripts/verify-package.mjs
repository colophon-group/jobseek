import { access, readFile } from "node:fs/promises";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createServer } from "../dist/server.js";

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

// Exercise the published protocol boundary. This catches SDK/Zod integration
// regressions that package metadata and TypeScript compilation cannot detect,
// including schemas that the MCP SDK can register but cannot serialize.
const expectedToolSchemas = {
  create_watchlist_link: {
    properties: [
      "companies",
      "description",
      "exp",
      "loc",
      "locale",
      "occ",
      "q",
      "sal",
      "salcur",
      "sen",
      "tech",
      "title",
    ],
    required: ["title"],
  },
  get_discovery_results: { properties: [], required: [] },
  get_ghost_analysis: {
    properties: ["position", "runId"],
    required: ["runId"],
  },
  get_job_detail: { properties: ["id", "locale"], required: ["id"] },
  list_taxonomies: {
    properties: ["locale", "type"],
    required: ["type"],
  },
  resolve_slugs: {
    properties: ["locale", "q", "type"],
    required: ["q", "type"],
  },
  search_companies: {
    properties: ["locale", "q"],
    required: ["q"],
  },
  search_jobs: {
    properties: ["exp", "loc", "locale", "occ", "q", "sal", "sen", "tech"],
    required: [],
  },
  search_watchlists: { properties: ["locale", "q"], required: [] },
  trigger_batch_ghost_analysis: {
    properties: ["companies"],
    required: ["companies"],
  },
  trigger_discovery_run: {
    properties: ["enableAiDiscovery", "sources"],
    required: [],
  },
  trigger_ghost_analysis: {
    properties: ["companyName", "inventoryMode", "maxSnapshots", "portalUrl"],
    required: ["portalUrl"],
  },
};
const expectedTools = Object.keys(expectedToolSchemas).sort();

const server = createServer("https://example.invalid");
const client = new Client({ name: "jobseek-package-verifier", version: "1.0.0" });
const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();

try {
  await Promise.all([
    server.connect(serverTransport),
    client.connect(clientTransport),
  ]);

  const { tools } = await client.listTools();
  assert(
    JSON.stringify(tools.map(({ name }) => name).sort()) ===
      JSON.stringify(expectedTools),
    "MCP tool registry must preserve the documented public tool set",
  );
  for (const tool of tools) {
    assert(
      tool.inputSchema?.type === "object" &&
        typeof tool.inputSchema.properties === "object",
      `MCP tool ${tool.name} must expose a serializable object input schema`,
    );
    const actualSchema = {
      properties: Object.keys(tool.inputSchema.properties ?? {}).sort(),
      required: [...(tool.inputSchema.required ?? [])].sort(),
    };
    assert(
      JSON.stringify(actualSchema) ===
        JSON.stringify(expectedToolSchemas[tool.name]),
      `MCP tool ${tool.name} must preserve its public input interface`,
    );
  }

  const { resourceTemplates } = await client.listResourceTemplates();
  assert(
    resourceTemplates.some(
      ({ uriTemplate }) => uriTemplate === "jobseek://taxonomies/{type}",
    ),
    "MCP taxonomy resource template must remain registered",
  );
} finally {
  await Promise.allSettled([client.close(), server.close()]);
}
