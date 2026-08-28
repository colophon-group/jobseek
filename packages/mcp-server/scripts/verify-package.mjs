import { access, readFile } from "node:fs/promises";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createServer } from "../dist/server.js";
import { JobseekClient } from "../dist/client.js";
import {
  API_LOCALES,
  DEFAULT_API_LOCALE,
  PUBLIC_API_VERSION,
  PUBLIC_SEARCH_QUERY_PARAMETERS,
  SEARCH_EMPLOYMENT_TYPE_LIST_PATTERN,
  SEARCH_EMPLOYMENT_TYPE_VALUES,
  SEARCH_INTEGER_RANGE_PATTERN,
  SEARCH_LANGUAGE_LIST_PATTERN,
  SEARCH_WORK_MODE_LIST_PATTERN,
  SEARCH_WORK_MODE_VALUES,
} from "../dist/public-api-contract.js";

const packageJson = JSON.parse(await readFile("package.json", "utf8"));
const serverJson = JSON.parse(await readFile("server.json", "utf8"));
const openApi = JSON.parse(
  await readFile(
    new URL("../../../apps/web/public/api/openapi.json", import.meta.url),
    "utf8",
  ),
);

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

assert(
  packageJson.repository?.url ===
    "git+https://github.com/colophon-group/jobseek.git",
  "package.json repository.url must identify the trusted-publisher repository",
);
assert(
  packageJson.repository?.directory === "packages/mcp-server",
  "package.json repository.directory must identify this workspace package",
);

// Exercise the published protocol boundary. This catches SDK/Zod integration
// regressions that package metadata and TypeScript compilation cannot detect,
// including schemas that the MCP SDK can register but cannot serialize.
const expectedToolSchemas = {
  create_watchlist_link: {
    properties: [
      "companies",
      "description",
      "etype",
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
      "wm",
    ],
    required: ["title"],
  },
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
    properties: [...PUBLIC_SEARCH_QUERY_PARAMETERS].sort(),
    required: [],
  },
  search_watchlists: { properties: ["locale", "q"], required: [] },
  trigger_batch_ghost_analysis: {
    properties: ["companies"],
    required: ["companies"],
  },
  trigger_ghost_analysis: {
    properties: ["companyName", "inventoryMode", "maxSnapshots", "portalUrl"],
    required: ["portalUrl"],
  },
};
const expectedTools = Object.keys(expectedToolSchemas).sort();
assert(
  openApi.info?.version === PUBLIC_API_VERSION,
  "OpenAPI info.version must match the public API contract",
);
const openApiSearchParameters = openApi.paths?.["/api/v1/search"]?.get?.parameters;
assert(
  Array.isArray(openApiSearchParameters),
  "OpenAPI must define /api/v1/search GET parameters",
);
const openApiSearchNames = openApiSearchParameters
  .filter((parameter) => parameter.in === "query")
  .map((parameter) => parameter.name)
  .sort();
assert(
  JSON.stringify(openApiSearchNames) ===
    JSON.stringify([...PUBLIC_SEARCH_QUERY_PARAMETERS].sort()),
  "OpenAPI search query parameters must match the public API contract",
);

function openApiQueryParameter(path, name) {
  return openApi.paths?.[path]?.get?.parameters?.find(
    (parameter) => parameter.in === "query" && parameter.name === name,
  );
}

for (const path of ["/api/v1/search", "/api/v1/watchlist/create"]) {
  const workModeParameter = openApiQueryParameter(path, "wm");
  assert(
    JSON.stringify(workModeParameter?.schema?.items?.enum) ===
      JSON.stringify(SEARCH_WORK_MODE_VALUES),
    `OpenAPI ${path} work-mode values must match the public API contract`,
  );
  const employmentTypeParameter = openApiQueryParameter(path, "etype");
  assert(
    JSON.stringify(employmentTypeParameter?.schema?.items?.enum) ===
      JSON.stringify(SEARCH_EMPLOYMENT_TYPE_VALUES),
    `OpenAPI ${path} employment-type values must match the public API contract`,
  );
}
const languageParameter = openApiQueryParameter("/api/v1/search", "lang");
assert(
  JSON.stringify(languageParameter?.schema?.items?.enum) ===
    JSON.stringify(API_LOCALES),
  "OpenAPI search language values must match the public API contract",
);
for (const name of ["sal", "exp"]) {
  assert(
    openApiQueryParameter("/api/v1/search", name)?.schema?.pattern ===
      SEARCH_INTEGER_RANGE_PATTERN,
    `OpenAPI search ${name} pattern must match the public API contract`,
  );
}

for (const [path, pathItem] of Object.entries(openApi.paths ?? {})) {
  const parameters = pathItem?.get?.parameters;
  assert(Array.isArray(parameters), `OpenAPI ${path} must define GET parameters`);
  const localeParameter = parameters.find(
    (parameter) => parameter.in === "query" && parameter.name === "locale",
  );
  assert(localeParameter, `OpenAPI ${path} must define the shared locale query`);
  assert(
    JSON.stringify(localeParameter.schema?.enum) === JSON.stringify(API_LOCALES),
    `OpenAPI ${path} locale enum must match the public API contract`,
  );
  assert(
    localeParameter.schema?.default === DEFAULT_API_LOCALE,
    `OpenAPI ${path} locale default must match the public API contract`,
  );
  assert(
    pathItem.get?.responses?.["400"],
    `OpenAPI ${path} must document invalid-request responses`,
  );
}

const server = createServer("https://example.invalid");
const client = new Client({ name: "jobseek-package-verifier", version: "1.0.0" });
const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();

try {
  await Promise.all([
    server.connect(serverTransport),
    client.connect(clientTransport),
  ]);

  const { tools } = await client.listTools();
  const retiredTools = ["get_discovery_results", "trigger_discovery_run"];
  assert(
    retiredTools.every((name) => !tools.some((tool) => tool.name === name)),
    "MCP tool registry must not restore the retired vendor-backed discovery tools",
  );
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
    const localeSchema = tool.inputSchema.properties?.locale;
    if (localeSchema) {
      assert(
        JSON.stringify(localeSchema.enum) === JSON.stringify(API_LOCALES),
        `MCP tool ${tool.name} locale enum must match the public API contract`,
      );
      assert(
        localeSchema.default === DEFAULT_API_LOCALE,
        `MCP tool ${tool.name} locale default must match the public API contract`,
      );
    }
  }

  const searchTool = tools.find(({ name }) => name === "search_jobs");
  const searchPatterns = {
    wm: SEARCH_WORK_MODE_LIST_PATTERN,
    etype: SEARCH_EMPLOYMENT_TYPE_LIST_PATTERN,
    sal: SEARCH_INTEGER_RANGE_PATTERN,
    exp: SEARCH_INTEGER_RANGE_PATTERN,
    lang: SEARCH_LANGUAGE_LIST_PATTERN,
  };
  for (const [name, pattern] of Object.entries(searchPatterns)) {
    assert(
      searchTool?.inputSchema?.properties?.[name]?.pattern === pattern,
      `MCP search_jobs ${name} pattern must match the public API contract`,
    );
  }
  const createWatchlistTool = tools.find(
    ({ name }) => name === "create_watchlist_link",
  );
  for (const [name, pattern] of Object.entries({
    wm: SEARCH_WORK_MODE_LIST_PATTERN,
    etype: SEARCH_EMPLOYMENT_TYPE_LIST_PATTERN,
  })) {
    assert(
      createWatchlistTool?.inputSchema?.properties?.[name]?.pattern === pattern,
      `MCP create_watchlist_link ${name} pattern must match the public API contract`,
    );
  }

  const originalFetch = globalThis.fetch;
  const requestedRequests = [];
  globalThis.fetch = async (input, init) => {
    requestedRequests.push({
      url: String(input),
      method: init?.method ?? "GET",
      headers: new Headers(init?.headers),
    });
    return new Response(JSON.stringify({ companies: [], totalCompanies: 0 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const validSearch = await client.callTool({
      name: "search_jobs",
      arguments: {
        q: "engineer",
        loc: "switzerland",
        occ: "software-engineer",
        sen: "senior",
        tech: "typescript",
        wm: "remote,hybrid",
        etype: "full_time,contract",
        sal: "80000-150000",
        exp: "3-10",
        lang: "en,de",
        locale: "fr",
      },
    });
    assert(!validSearch.isError, "MCP search_jobs must accept the shared contract");
    assert(
      requestedRequests.length === 1,
      "MCP search_jobs must issue exactly one API request",
    );
    const forwarded = new URL(requestedRequests[0].url);
    for (const name of PUBLIC_SEARCH_QUERY_PARAMETERS) {
      assert(
        forwarded.searchParams.has(name),
        `MCP search_jobs must forward ${name} to the REST API`,
      );
    }
    assert(
      requestedRequests[0].headers.get("x-jobseek-internal-mcp-token") === null,
      "The published/default MCP server must not send a hosted provenance token",
    );

    requestedRequests.length = 0;
    const invalidSearch = await client.callTool({
      name: "search_jobs",
      arguments: { wm: "remote,bogus" },
    });
    assert(invalidSearch.isError, "MCP search_jobs must reject unsupported filter values");
    assert(
      requestedRequests.length === 0,
      "MCP search_jobs must reject invalid input before calling the REST API",
    );

    const internalToken = "SECRET_HOSTED_MCP_PROVENANCE_CANARY";
    const hostedClient = new JobseekClient("https://example.invalid", {
      internalMcpToken: internalToken,
    });
    await hostedClient.get("/api/v1/job", { id: "job-1" });
    await hostedClient.post("/api/v1/internal-test", { ok: true });

    assert(
      requestedRequests.length === 2,
      "A configured client must issue the expected GET and POST requests",
    );
    for (const request of requestedRequests) {
      assert(
        request.headers.get("x-jobseek-internal-mcp-token") === internalToken,
        `A configured ${request.method} must send the hosted provenance token header`,
      );
      assert(
        !request.url.includes(internalToken),
        "The hosted provenance token must never be added to a request URL",
      );
    }
  } finally {
    globalThis.fetch = originalFetch;
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
