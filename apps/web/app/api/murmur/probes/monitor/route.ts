/**
 * POST /api/murmur/probes/monitor — Murmur subcommand `probe monitor`.
 *
 * Validates `board_url` via the SSRF allowlist (J4). Lib function:
 * `probe_monitor` in `apps/crawler/src/workspace/lib/probe.py`.
 *
 * @see colophon-group/jobseek#2759
 */

import { handleSubcommand } from "../../_lib/handle";
import { PROBE_MONITOR_SCHEMA } from "../../_lib/schemas";

export async function POST(request: Request) {
  return handleSubcommand(request, {
    libSubcommand: "probe_monitor",
    schema: PROBE_MONITOR_SCHEMA,
    urlFields: ["board_url"],
  });
}
