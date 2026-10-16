# Go and Lightpanda migration delivery plan

Full delivery remains **active and incomplete**. Preserve every enabled board;
complete production Go execution, native ownership lifecycle and recovery, then
remove production Python, Playwright and Chromium. Keep useful offline Python.

## Current checkpoint — 2026-10-16

**0.13.1075** is independently serving from source
`cbd1f37f3803a29dcd6693aa0a120992513edda6`, with latest stable Lightpanda **1.0.1**
and its installed official ARM binary checksum verified. Both original deployments,
supported cold activation, exact-head CI and physical/ARM qualification passed.

The full **7,833-monitor / 7,832-detail** cohort is active at epoch **356**,
preserving every previous member and adding seven details across four profiles.
SQL, Redis, receipts, all ten actors and eight health endpoints agree. Postactive
compiled replay preserves the identical full cohort and every captured route,
with **zero owned cache mismatches**. The capture has **1,717,574 scheduled and
queued IDs**, with zero captured gap. Every one of the **2,354 outgoing-only IDs**
is currently inactive or unscheduled; none remains eligible or missing. These are
non-atomic live captures and current eligibility accounting, not a historical or
global zero-loss guarantee.

The retained-number action fix is deployed. All 12 current owned configurations
that failed their opaque copy before the fix pass afterward. Source 1074 had
three observed action-related restarts; the current source has **zero observed
restarts, OOMs or fatal configuration failures** through the bounded readback at
07:53 UTC, over an hour after serving verification. Continue observing it.

The 06:49 UTC production snapshot records 59 monitor and 205 detail completion receipts,
and 424 successful exports with zero errors or unknown writers. Completion
receipts are not successful-content proof. Freshness remains unfinished: about
**1.61 million scheduled postings are over 24 hours overdue**. Isolated ARM density
qualification does not establish matched production fleet cost.

See the [1075 delivery evidence](evidence/go-native1075-delivery-2026-10-16.json)
and [portable checkpoint](33-go-lightpanda-delivery-checkpoint-2026-10-16.md)
for exact workflows, images, limits and continuation order.

## Continue in substantial shared-contract batches

After the delivered seven details, the captured frontier is **64 monitors / 65
details**: 19/30 already match compiled profiles, while 45/35 remain unsupported
under their full current configuration. Counts require a fresh census if data
changes. Preserve the admitted floor through every subsequent batch.

Prioritize shared browser transport and configuration controls across DOM,
JSON-LD, API-browser, inline and Njoyn. The provider implementations already exist;
unsupported configuration does not imply that another provider port is needed.
An offline diagnostic that removed transport controls exposed existing parsers
for 18 more monitors and 29 details. Those altered configurations are
**unqualified**. Prove original semantics and actual content/lifecycle behavior
before accepting any replacement transport or browser-option change.

[Lightpanda supports proxy configuration](https://lightpanda.io/docs/guides/configure-a-proxy),
while the production renderer currently has no admitted proxy path. Current
unsupported configs include 19 monitor and 26 detail proxy requests, overlapping
other controls. Test a shared native transport against the full original config,
with bounded attempts, existing publisher policy, egress isolation and scoped
credentials. These occurrence counts are opportunities, not promised admissions.

Candidate **0.13.1076** implements configured proxy sessions for DOM monitors and
DOM, JSON-LD, embedded and Nextdata details, shared endpoint health, and the
original one-to-five origin-block attempts. The protected proxy registry belongs
to the immutable renderer release. Local physical and canonical lifecycle tests
pass; this candidate is **not installed and grants no new production ownership**.
Finish exact-head CI, ARM physical/rollback checks and actual full-config
original/native comparisons before the supported rollout and cohort expansion.
API-browser conversations, inline and other unsupported controls still need work.

Qualify multiple profile families per iteration. Keep the serving release and its qualification intact while developing the next
batch in an isolated worktree. Reuse the successful installed source for unchanged
compiled profiles when possible; ownership expansion still needs the supported
B0 incarnation rotation and fresh full-cohort admission.
Use actual original/native inventory, fields and canonical lifecycle evidence;
original fetch errors do not establish an empty inventory. Resolve rendering,
pagination and source-identity differences without dropping fields or members.

## Full-delivery exit criteria

- Every enabled monitor/detail route has a proved Go replacement and current
  canonical write/queue/freshness behavior; no enabled-board coverage reduction.
- Deployment, CSV publication, native ownership handoff, maintenance and rollback
  work through supported committed workflows with exact source/image identity.
- Comparable whole-lane CPU/RAM/density and attributable cost evidence includes
  all relevant actors and uses matched work with explicit measurement limits.
- Supported cold reversal and the recovery window are proved before deleting
  production Python/Playwright/Chromium and their runtime-only assets.

Use the [portable delivery checkpoint](33-go-lightpanda-delivery-checkpoint-2026-10-16.md)
and its linked evidence for the detailed prior proofs. Earlier chronological
notes are preserved in [Git history](https://github.com/colophon-group/jobseek/blob/cbd1f37f3803a29dcd6693aa0a120992513edda6/docs/27-go-lightpanda-continuation-plan.md).
