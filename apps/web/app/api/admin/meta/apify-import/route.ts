import { NextResponse } from "next/server";
import { matchesBasicAuthorization } from "@/lib/admin/basic-auth";
import {
  MetaApifyImportError,
  importLatestMetaApifyRun,
} from "@/lib/admin/meta-apify-import";

export const runtime = "nodejs";

function unauthorizedResponse(): NextResponse {
  return new NextResponse("Unauthorized", {
    status: 401,
    headers: {
      "WWW-Authenticate": "Basic",
    },
  });
}

export async function POST(request: Request) {
  if (
    !matchesBasicAuthorization(
      request.headers.get("authorization"),
      process.env.ADMIN_SECRET,
    )
  ) {
    return unauthorizedResponse();
  }

  try {
    const result = await importLatestMetaApifyRun();
    return NextResponse.json(result);
  } catch (error) {
    if (error instanceof MetaApifyImportError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }

    return NextResponse.json(
      { error: "Meta Apify import failed" },
      { status: 500 },
    );
  }
}
