/**
 * POST /api/murmur/select/monitor — Murmur subcommand `select monitor`.
 *
 * No URL fetched here; `board_url` is recorded for state but not
 * dereferenced. Lib function: `select_monitor` in
 * `apps/crawler/src/workspace/lib/select.py`. The shim writes the
 * named-config slot to the per-claim KV (Postgres-backed).
 *
 * @see colophon-group/jobseek#2759
 */

import { handleSubcommand } from "../../_lib/handle";
import { SELECT_MONITOR_SCHEMA } from "../../_lib/schemas";

export async function POST(request: Request) {
  return handleSubcommand(request, {
    libSubcommand: "select_monitor",
    schema: SELECT_MONITOR_SCHEMA,
    // We still SSRF-validate the supplied board_url defensively even
    // though no fetch happens here — keeps surface uniform across
    // routes and prevents accidental log/replay of a bogus URL.
    urlFields: ["board_url"],
  });
}
