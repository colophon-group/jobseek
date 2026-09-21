import { NextResponse } from "next/server";

import {
  disableAiFilterConfiguration,
  getAiFilterOwnerState,
  putAiFilterConfiguration,
} from "@/lib/ai-filter/configuration-service";
import {
  AI_FILTER_PRIVATE_HEADERS,
  aiFilterErrorResponse,
  aiFilterNotFoundResponse,
  isUuid,
  readSmallJson,
} from "@/lib/ai-filter/route-utils";
import { startAiFilterCatchup } from "@/lib/ai-filter/workflow-trigger";
import { getSessionUserIdFromHeaders } from "@/lib/sessionCache";

type Context = { params: Promise<{ id: string }> };

async function ownerAndId(request: Request, context: Context) {
  const [ownerId, { id: watchlistId }] = await Promise.all([
    getSessionUserIdFromHeaders(request.headers),
    context.params,
  ]);
  return ownerId && isUuid(watchlistId) ? { ownerId, watchlistId } : null;
}

export async function GET(request: Request, context: Context) {
  const owner = await ownerAndId(request, context);
  if (!owner) return aiFilterNotFoundResponse();
  try {
    const state = await getAiFilterOwnerState(owner);
    return NextResponse.json(state, { headers: AI_FILTER_PRIVATE_HEADERS });
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}

export async function PUT(request: Request, context: Context) {
  const owner = await ownerAndId(request, context);
  if (!owner) return aiFilterNotFoundResponse();
  try {
    const body = await readSmallJson(request);
    if (Reflect.ownKeys(body).length !== 1 || !("query" in body)) {
      throw new TypeError("AI filter configuration contains unsupported fields");
    }
    const state = await putAiFilterConfiguration({ ...owner, query: body.query });
    const workflow = await startAiFilterCatchup(owner);
    return NextResponse.json(
      { state, workflow },
      { status: 202, headers: AI_FILTER_PRIVATE_HEADERS },
    );
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}

export async function DELETE(request: Request, context: Context) {
  const owner = await ownerAndId(request, context);
  if (!owner) return aiFilterNotFoundResponse();
  try {
    await disableAiFilterConfiguration(owner);
    return new Response(null, { status: 204, headers: AI_FILTER_PRIVATE_HEADERS });
  } catch (error) {
    return aiFilterErrorResponse(error);
  }
}
