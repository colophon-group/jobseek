# Job Seek dataset access and retained-content register

27 September 2026. Follow-up to #10079 and #10091. Both Job Seek Hugging Face
repositories are private. A successful private-visibility check is now required
before labelled uploads, trace uploads, trace backfills and live scrub writes.
Missing/inaccessible/public metadata stops the operation; no code path in these
uploaders creates a repository or makes one public. Concurrent administrative
visibility changes remain outside this preflight guarantee.

## Retained material and purpose

| Dataset | Material | Purpose and basis status |
| --- | --- | --- |
| jobseek-postings-labelled | Original annotations/schemas, source HTML, normalized descriptions and some verbatim labels | Internal extraction development, including commercial product development. Original contributions have a scoped CC-BY grant; third-party text does not. Source-specific retention/processing grounds remain part of #10079. |
| jobseek-agent-traces | Agent trajectories, prompts, tool results and possible source text | Internal workflow development and audit. MIT applies only to Job Seek-owned software/documentation contributions; third-party tool/source material is excluded. Credential scanning exists, but is not a content licence or a full personal-data audit. |

Private access contains current disclosure, without creating rights in retained
source material or recalling earlier downloads. There is no evidence here of a
universal retention period or employer permission. A future public release needs
both a documented redistribution basis and a new owner instruction.

## Existing limited removal support

`labeller scrub --slug <slug> --dry-run` can inventory affected dated JSONLs;
its live form rewrites matching releases and deletes an empty date file. Add
an accepted company opt-out to `apps/crawler/data/labeller_optout.txt` to prevent
future sampling/upload. The card refresh now counts surviving remote files,
not whichever files happen to exist in the operator's local checkout. Synthetic
tests cover multi-date removal, the last row, malformed source refusal and
future upload filtering. No live content-removal run was performed for this audit.

A normal Hugging Face commit changes the current tree, not old commits, branches,
external downloads or caches. No history erasure or cache-removal guarantee is
made. A real request needs a scope-specific plan and platform-supported handling
of any retained history. Keep only necessary non-content audit evidence.

**Owner decision:** the complete cross-system removal procedure will be
implemented upon the first request, not as a Paddle pre-verification condition.
This includes coordinating source re-ingestion, database/index copies, R2
content, logos, caches, other datasets and restoration behavior. The existing
scrub command alone is not an end-to-end takedown implementation.
