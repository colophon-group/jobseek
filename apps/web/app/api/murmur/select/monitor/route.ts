/**
 * POST /api/murmur/select/monitor — Murmur subcommand `select monitor`.
 *
 * No URL fetched here; `board_url` is recorded for state but not
 * dereferenced. Lib function: `select_monitor` in
 * `apps/crawler/src/workspace/lib/select.py`. The shim writes the
 * named-config slot to the per-claim KV (Postgres-backed).
 *
 * Demo limitation: the shim currently stores the agent's
 * `candidate_id` as both the `monitor_type` and `name` in the slot
 * (see `cli_shim._do_select_monitor`). That's fine for the M0
 * envelope contract — `select_monitor` succeeds and a subsequent
 * `run_monitor` reads the slot back — but a real `run_monitor`
 * against a live board would fail unless `candidate_id` happens to
 * match a registered monitor type. A production implementation must
 * resolve `candidate_id` against the prior probe's candidate list and
 * persist the actual `(monitor_type, monitor_config)` pair.
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
