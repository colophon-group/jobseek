/**
 * POST /api/murmur/run/scraper — Murmur subcommand `run scraper`.
 *
 * Validates `board_url` and (optionally) `sample_job_url` via the SSRF
 * allowlist (J4). Lib function: `run_scraper` in
 * `apps/crawler/src/workspace/lib/run.py`. The shim resolves the active
 * named scraper config from `PostgresClaimKV` before the call.
 *
 * @see colophon-group/jobseek#2759
 */

import { handleSubcommand } from "../../_lib/handle";
import { RUN_SCRAPER_SCHEMA } from "../../_lib/schemas";

export async function POST(request: Request) {
  return handleSubcommand(request, {
    libSubcommand: "run_scraper",
    schema: RUN_SCRAPER_SCHEMA,
    urlFields: ["board_url", "sample_job_url"],
  });
}
