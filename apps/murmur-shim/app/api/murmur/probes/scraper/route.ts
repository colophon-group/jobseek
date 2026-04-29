/**
 * POST /api/murmur/probes/scraper — Murmur subcommand `probe scraper`.
 *
 * Validates `board_url` via the SSRF allowlist (J4). The agent does not
 * supply `sample_url` here; the lib falls back to URLs already captured
 * by a prior monitor run. Lib function: `probe_scraper` in
 * `apps/crawler/src/workspace/lib/probe.py`.
 *
 * @see colophon-group/jobseek#2759
 */

import { handleSubcommand } from "../../_lib/handle";
import { PROBE_SCRAPER_SCHEMA } from "../../_lib/schemas";

export async function POST(request: Request) {
  return handleSubcommand(request, {
    libSubcommand: "probe_scraper",
    schema: PROBE_SCRAPER_SCHEMA,
    urlFields: ["board_url"],
  });
}
