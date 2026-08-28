import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { createServer } from "./server.js";
import type { JobseekClientOptions } from "./client.js";

/**
 * Stateless MCP request handler for serverless environments.
 * Each request creates a fresh transport+server, processes it, and returns.
 */
export async function handleMcpRequest(
  req: Request,
  baseUrl = "https://jseek.co",
  options: JobseekClientOptions = {},
): Promise<Response> {
  if (req.method === "DELETE") {
    return new Response(null, { status: 200 });
  }

  const transport = new WebStandardStreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
  });
  const server = createServer(baseUrl, options);
  await server.connect(transport);
  return transport.handleRequest(req);
}
