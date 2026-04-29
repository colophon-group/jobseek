/**
 * POST /api/murmur/feedback — Murmur subcommand `feedback`.
 *
 * No URL fields. Lib function: `feedback` in
 * `apps/crawler/src/workspace/lib/feedback.py`. Records the verdict on
 * the claim's currently-active named config in the per-claim KV.
 *
 * @see colophon-group/jobseek#2759
 */

import { handleSubcommand } from "../../_lib/handle";
import { FEEDBACK_SCHEMA } from "../../_lib/schemas";

export async function POST(request: Request) {
  return handleSubcommand(request, {
    libSubcommand: "feedback",
    schema: FEEDBACK_SCHEMA,
    urlFields: [],
  });
}
