/**
 * POST /api/murmur/select/scraper — Murmur subcommand `select scraper`.
 *
 * Lib function: `select_scraper` in
 * `apps/crawler/src/workspace/lib/select.py`. Per-claim KV write only;
 * the URL is validated defensively but not dereferenced.
 *
 * @see colophon-group/jobseek#2759
 */

import { handleSubcommand } from "../../_lib/handle";
import { SELECT_SCRAPER_SCHEMA } from "../../_lib/schemas";

export async function POST(request: Request) {
  return handleSubcommand(request, {
    libSubcommand: "select_scraper",
    schema: SELECT_SCRAPER_SCHEMA,
    urlFields: ["board_url"],
  });
}
