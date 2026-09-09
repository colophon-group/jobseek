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
  if (req.method === "GET") {
    // This handler creates an isolated transport for every request, so a
    // standalone SSE stream cannot receive messages from later POSTs. Leaving
    // it open only pins the serverless invocation until the platform timeout.
    return new Response(null, {
      status: 405,
      headers: { Allow: "POST, DELETE" },
    });
  }

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
