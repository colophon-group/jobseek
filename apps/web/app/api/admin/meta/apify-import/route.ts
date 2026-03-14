import { NextResponse } from "next/server";
import { matchesBasicAuthorization } from "@/lib/admin/basic-auth";
import {
  MetaApifyImportError,
  importLatestMetaApifyRun,
} from "@/lib/admin/meta-apify-import";

export const runtime = "nodejs";

function apifyDebug(message: string, details?: Record<string, unknown>): void {
  if (details) {
    console.info("APIFY_DEBUG", message, details);
    return;
  }

  console.info("APIFY_DEBUG", message);
}

function unauthorizedResponse(): NextResponse {
  return new NextResponse("Unauthorized", {
    status: 401,
    headers: {
      "WWW-Authenticate": "Basic",
    },
  });
}

export async function POST(request: Request) {
  apifyDebug("route request received");
  if (
    !matchesBasicAuthorization(
      request.headers.get("authorization"),
      process.env.ADMIN_SECRET,
    )
  ) {
    apifyDebug("route unauthorized");
    return unauthorizedResponse();
  }

  try {
    const result = await importLatestMetaApifyRun();
    apifyDebug("route success", {
      fetched: result.fetched,
      inserted: result.inserted,
      updated: result.updated,
    });
    return NextResponse.json(result);
  } catch (error) {
    if (error instanceof MetaApifyImportError) {
      apifyDebug("route handled error", {
        status: error.status,
        message: error.message,
      });
      return NextResponse.json({ error: error.message }, { status: error.status });
    }

    apifyDebug("route unhandled error", {
      message: error instanceof Error ? error.message : "Unknown error",
    });
    return NextResponse.json(
      { error: "Meta Apify import failed" },
      { status: 500 },
    );
  }
}
