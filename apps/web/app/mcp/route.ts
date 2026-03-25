import { handleMcpRequest } from "@jseek/mcp-server/handler";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Accept, Mcp-Session-Id, Last-Event-ID, Mcp-Protocol-Version",
  "Access-Control-Expose-Headers": "Mcp-Session-Id",
};

function withCors(response: Response): Response {
  for (const [key, value] of Object.entries(CORS_HEADERS)) {
    response.headers.set(key, value);
  }
  return response;
}

export async function OPTIONS() {
  return new Response(null, { status: 204, headers: CORS_HEADERS });
}

export async function POST(req: Request) {
  return withCors(await handleMcpRequest(req));
}

export async function GET(req: Request) {
  return withCors(await handleMcpRequest(req));
}

export async function DELETE(req: Request) {
  return withCors(await handleMcpRequest(req));
}
