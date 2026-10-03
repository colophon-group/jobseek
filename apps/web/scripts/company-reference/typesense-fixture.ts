import { createServer } from "node:http";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import { companyDocument } from "./fixture";

/** HTTP boundary fixture used by the unmodified Typesense SDK in the actual Next app. */
export async function startTypesenseFixture(companies: ReturnType<typeof companyDocument>[], delays: Record<string, number> = {}) {
  const requests: { pathname: string; method: string; filter?: string; delayMs?: number }[] = [];
  const server = createServer(async (request, response) => {
    const url = new URL(request.url!, "http://localhost");
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    const body = Buffer.concat(chunks).toString();
    let delayMs = 0;
    // Delay only server-side UUID preparation, leaving the user's name picker responsive.
    if (url.pathname === "/collections/company/documents/search") {
      const filter = url.searchParams.get("filter_by") ?? "";
      for (const [id, milliseconds] of Object.entries(delays)) {
        if (filter.includes(id)) { delayMs += milliseconds; await new Promise(resolve => setTimeout(resolve, milliseconds)); }
      }
    }
    const search = (query: Record<string, unknown>) => {
      const collection = query.collection ?? url.pathname.split("/")[2];
      const filter = String(query.filter_by ?? "");
      requests.push({ pathname: url.pathname, method: request.method!, filter, delayMs });
      if (collection !== "company") {
        return { found: 0, hits: [], facet_counts: [], grouped_hits: [], search_time_ms: 1, page: 1 };
      }
      const q = String(query.q ?? "*").toLowerCase();
      const docs = companies.filter(doc => (q === "*" || doc.name.toLowerCase().includes(q)) &&
        (!filter || (!/\b(id|slug):/.test(filter)) || filter.includes(doc.id) || filter.includes(doc.slug)));
      return { found: docs.length, hits: docs.map(document => ({ document, highlights: [], text_match: 1 })), facet_counts: [], search_time_ms: 1, page: 1 };
    };
    response.setHeader("content-type", "application/json");
    // A deliberately cache-missing Redis REST fixture. Session identity always
    // reaches real Better Auth/PostgreSQL; limiter commands receive ample local
    // capacity so an absent external cache does not dominate browser latency.
    if (url.pathname.startsWith("/redis")) {
      const command = (parts: unknown[]) => {
        const name = String(parts[0]).toUpperCase();
        if (["EVAL", "EVALSHA"].includes(name)) return { result: [1000, 1000] };
        if (name === "GET") return { result: null };
        if (name === "MGET") return { result: parts.slice(1).map(() => null) };
        if (name === "SCAN") return { result: ["MA==", []] };
        if (["SET", "SETEX", "MSET"].includes(name)) return { result: "OK" };
        if (["DEL", "EXPIRE", "INCR", "DECR"].includes(name)) return { result: 1 };
        return { error: "Unsupported local Redis fixture command" };
      };
      const payload = JSON.parse(body);
      return response.end(JSON.stringify(url.pathname.endsWith("/pipeline") || url.pathname.endsWith("/multi-exec") ? payload.map(command) : command(payload)));
    }
    if (url.pathname === "/health") return response.end('{"ok":true}');
    if (url.pathname === "/multi_search") {
      return response.end(JSON.stringify({ results: JSON.parse(body).searches.map(search) }));
    }
    if (url.pathname.endsWith("/documents/search")) return response.end(JSON.stringify(search(Object.fromEntries(url.searchParams))));
    const match = url.pathname.match(/^\/collections\/company\/documents\/([^/]+)$/);
    if (match) {
      requests.push({ pathname: url.pathname, method: request.method! });
      const doc = companies.find(doc => doc.id === decodeURIComponent(match[1]));
      if (doc) return response.end(JSON.stringify(doc));
      response.statusCode = 404; return response.end('{"message":"Not found"}');
    }
    response.statusCode = 404; response.end('{"message":"Unsupported fixture request"}');
  });
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  return { server, requests, port: (server.address() as AddressInfo).port };
}
