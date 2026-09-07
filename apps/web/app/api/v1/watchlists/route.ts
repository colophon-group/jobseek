import { type NextRequest } from "next/server";
import { PUBLIC_WATCHLIST_DISCOVERY_SUNSET } from "@jseek/mcp-server/public-api-contract";
import { apiResponse } from "../_shared";
import { withPublicApiObservability } from "@/lib/public-api-observability";

async function handleGet(_request: NextRequest) {
  // Keep this response independent of query parameters, sessions, and stored
  // watchlists. The route exists only for the bounded compatibility window;
  // authenticated owner-scoped list/read access is deferred to #8343.
  const response = apiResponse(
    { error: "Public watchlist discovery is no longer available" },
    { status: 410 },
  );
  response.headers.set("Sunset", PUBLIC_WATCHLIST_DISCOVERY_SUNSET);
  return response;
}

export const GET = withPublicApiObservability("watchlists", handleGet);
