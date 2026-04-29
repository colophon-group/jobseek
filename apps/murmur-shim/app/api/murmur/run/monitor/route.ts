/**
 * POST /api/murmur/run/monitor — Murmur subcommand `run monitor`.
 *
 * Validates `board_url` via the SSRF allowlist (J4). Lib function:
 * `run_monitor` in `apps/crawler/src/workspace/lib/run.py`. The shim
 * reads the active named config out of `PostgresClaimKV` and projects
 * it into `BoardConfigState.monitor_type` / `monitor_config`.
 *
 * @see colophon-group/jobseek#2759
 */

import { handleSubcommand } from "../../_lib/handle";
import { RUN_MONITOR_SCHEMA } from "../../_lib/schemas";

export async function POST(request: Request) {
  return handleSubcommand(request, {
    libSubcommand: "run_monitor",
    schema: RUN_MONITOR_SCHEMA,
    urlFields: ["board_url"],
  });
}
