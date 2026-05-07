/**
 * POST /api/admin/murmur-demo/run
 *
 * Demo-only Murmur run trigger. Operator hits this from a side terminal
 * (curl) or a tiny admin form during demo rehearsal. Body:
 *
 *   { "company_name": string, "website": string }
 *
 * Behaviour:
 *   - Basic-auth gated (`Authorization: Basic <ADMIN_SECRET>`), same gate as
 *     `apps/web/app/api/admin/meta/apify-import/route.ts`. Returns 401
 *     without consulting any other config when auth is missing/wrong.
 *   - Feature-flagged behind `MURMUR_RUN_TRIGGER_ENABLED`. When the flag is
 *     unset / not "true", the route returns 503 and never calls Murmur. This
 *     is the belt to the suspenders the issue asks for: the existing
 *     `requestCompany` flow is never touched, and a stray request to this
 *     URL in a non-demo environment fails closed.
 *   - Calls `startRun` (which carries the typed-error contract). Maps
 *     `StartRunError.code` -> HTTP status:
 *         config_missing -> 503 (operator forgot the env)
 *         http_4xx       -> 502 (Murmur said the input was invalid)
 *         http_5xx       -> 502 (Murmur upstream)
 *         timeout        -> 504
 *         network        -> 502
 *         bad_response   -> 502
 *
 * NEVER LOGS THE TOKEN. Errors logged here include the StartRunError code
 * and message (which by construction does not include the token).
 *
 * @see colophon-group/jobseek#2762
 * @see Murmur DESIGN.md §3.4 (Publisher API)
 */
import { NextResponse } from "next/server";
import { matchesBasicAuthorization } from "@/lib/admin/basic-auth";
import { StartRunError, startRun } from "@/lib/murmur/start-run";

function unauthorized(): NextResponse {
  return new NextResponse("Unauthorized", {
    status: 401,
    headers: { "WWW-Authenticate": "Basic" },
  });
}

function isFeatureEnabled(): boolean {
  return process.env.MURMUR_RUN_TRIGGER_ENABLED === "true";
}

interface RequestBody {
  company_name?: unknown;
  website?: unknown;
}

function parseBody(parsed: unknown): { company_name: string; website: string } | null {
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return null;
  }
  const { company_name, website } = parsed as RequestBody;
  if (
    typeof company_name !== "string" ||
    company_name.trim().length === 0 ||
    typeof website !== "string" ||
    website.trim().length === 0
  ) {
    return null;
  }
  return { company_name: company_name.trim(), website: website.trim() };
}

function statusForCode(code: StartRunError["code"]): number {
  switch (code) {
    case "config_missing":
      return 503;
    case "timeout":
      return 504;
    case "http_4xx":
    case "http_5xx":
    case "network":
    case "bad_response":
      return 502;
    default:
      return 502;
  }
}

export async function POST(request: Request): Promise<NextResponse> {
  // 1. Auth gate first (same shape as meta/apify-import).
  if (
    !matchesBasicAuthorization(
      request.headers.get("authorization"),
      process.env.ADMIN_SECRET,
    )
  ) {
    return unauthorized();
  }

  // 2. Feature flag — fail closed when not explicitly enabled.
  if (!isFeatureEnabled()) {
    return NextResponse.json(
      { error: "Murmur run trigger is disabled" },
      { status: 503 },
    );
  }

  // 3. Parse + validate body.
  let raw: unknown;
  try {
    raw = await request.json();
  } catch {
    return NextResponse.json(
      { error: "Body must be JSON" },
      { status: 400 },
    );
  }
  const input = parseBody(raw);
  if (!input) {
    return NextResponse.json(
      {
        error:
          "Body must be { company_name: string (non-empty), website: string (non-empty) }",
      },
      { status: 400 },
    );
  }

  // 4. Trigger the run. All failure modes -> typed StartRunError.
  try {
    const { run_id } = await startRun(input);
    return NextResponse.json({ run_id }, { status: 200 });
  } catch (err) {
    if (err instanceof StartRunError) {
      console.error("[murmur-demo/run] startRun failed", {
        code: err.code,
        status: err.status,
        message: err.message,
      });
      return NextResponse.json(
        { error: err.message, code: err.code },
        { status: statusForCode(err.code) },
      );
    }
    // Defensive: startRun's contract is to never throw anything else.
    console.error("[murmur-demo/run] unexpected error", err);
    return NextResponse.json(
      { error: "Unexpected error triggering Murmur run" },
      { status: 500 },
    );
  }
}
