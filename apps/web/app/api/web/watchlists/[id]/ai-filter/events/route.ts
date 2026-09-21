import {
  listAiFilterEvents,
} from "@/lib/ai-filter/configuration-service";
import {
  AI_FILTER_PRIVATE_HEADERS,
  aiFilterErrorResponse,
  aiFilterNotFoundResponse,
  isUuid,
} from "@/lib/ai-filter/route-utils";
import { getSessionUserIdFromHeaders } from "@/lib/sessionCache";

export async function GET(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const [ownerId, { id: watchlistId }] = await Promise.all([
    getSessionUserIdFromHeaders(request.headers),
    context.params,
  ]);
  if (!ownerId || !isUuid(watchlistId)) return aiFilterNotFoundResponse();
  try {
    const rawAfter = new URL(request.url).searchParams.get("after") ?? "0";
    if (!/^\d{1,16}$/.test(rawAfter)) throw new TypeError("Invalid event cursor");
    const after = Number(rawAfter);
    if (!Number.isSafeInteger(after)) throw new TypeError("Invalid event cursor");
    const events = await listAiFilterEvents({ ownerId, watchlistId, after });
    const body = events.map((event) => JSON.stringify(event)).join("\n");
    return new Response(body ? `${body}\n` : "", {
      headers: {
        ...AI_FILTER_PRIVATE_HEADERS,
        "Content-Type": "application/x-ndjson; charset=utf-8",
        "X-AI-Filter-Next-Cursor": String(events.at(-1)?.sequence ?? after),
      },
    });
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}
