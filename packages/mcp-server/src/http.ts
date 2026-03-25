#!/usr/bin/env node
import { randomUUID } from "node:crypto";
import express from "express";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { createServer } from "./server.js";

const PORT = parseInt(process.env.PORT || "8080", 10);
const BASE_URL = process.env.BASE_URL || "https://jseek.co";

const app = express();
app.use(express.json());

// Health check
app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

// Session management: map session ID → transport + server
const sessions = new Map<
  string,
  { transport: StreamableHTTPServerTransport; server: ReturnType<typeof createServer> }
>();

function getOrCreateSession(sessionId: string | undefined) {
  if (sessionId && sessions.has(sessionId)) {
    return sessions.get(sessionId)!;
  }

  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: () => randomUUID(),
  });
  const server = createServer(BASE_URL);
  server.connect(transport);

  transport.onclose = () => {
    if (transport.sessionId) {
      sessions.delete(transport.sessionId);
    }
  };

  // Store after connect so sessionId is set
  if (transport.sessionId) {
    sessions.set(transport.sessionId, { transport, server });
  }

  return { transport, server };
}

// MCP endpoint
app.post("/mcp", (req, res) => {
  const sessionId = req.headers["mcp-session-id"] as string | undefined;
  const session = getOrCreateSession(sessionId);
  session.transport.handleRequest(req, res, req.body);
});

app.get("/mcp", (req, res) => {
  const sessionId = req.headers["mcp-session-id"] as string | undefined;
  if (!sessionId || !sessions.has(sessionId)) {
    res.status(400).json({ error: "Missing or invalid session ID" });
    return;
  }
  sessions.get(sessionId)!.transport.handleRequest(req, res);
});

app.delete("/mcp", (req, res) => {
  const sessionId = req.headers["mcp-session-id"] as string | undefined;
  if (sessionId && sessions.has(sessionId)) {
    const session = sessions.get(sessionId)!;
    session.transport.close();
    sessions.delete(sessionId);
  }
  res.status(200).end();
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`MCP HTTP server listening on port ${PORT}`);
});
