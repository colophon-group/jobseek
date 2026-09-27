# Workable Go monitor evidence — 2026-09-27

## Same-response comparison and local database parity

The normal Python runs passively captured `llms.txt` and `jobs.md` responses.
They were copied from existing worker files into a mode-0700 local directory
(`/tmp/jobseek-workable-proof-20260927`), with each file mode 0600. No new
publisher requests were made and no schedule was forced.

Offline comparison used Python's existing Markdown parser and the Go
`fallback-project` command from release candidate #10089, head
`f871d220b4b44f8f3a19c000cd73f6d9a203018c`. Each parser consumed the same
bytes. Advertised counts and every canonical URL matched. A read-only local
PostgreSQL query at about 14:21 UTC matched every active URL hash; each board
was enabled, active, and had zero consecutive failures.

| Board | Exact board ID | Jobs | SHA-256 of sorted URLs joined with newline |
|---|---|---:|---|
| Pix4D | `06e7e8f2-4741-49b2-a135-96cfeb3bcfdd` | 4 | `d89eb14092702fa22172c30257f0e8a5eff0988f309b2a0971a467d7d1b23486` |
| Debiopharm | `631e7fce-366e-409d-b372-4ee4870e74c1` | 7 | `bafa8297dffe909e2bcbc26345a2d57aced2e1f643e58c2782e9e1f9a27711b4` |
| Unit8 | `acecf5fd-3252-420d-a098-542cb1c0fc77` | 14 | `df29af80eaf266026689f43c5384b63c9918dc892361567953d886adff842461` |
| Hack The Box | `08c21917-9457-4b19-b0e6-40d0280a36c4` | 23 | `f7a605f9725854ce623555397c345b8587d0c6318e803f721791a6185adb5e95` |

The corresponding successful Python runs were at 13:53:46, 13:55:38,
14:00:37, and 14:07:38 UTC. This evidence proves parser parity on those
responses. It does not yet prove live Go execution or whole-lane efficiency.

## Exact-board activation

After successful #10089 deployment, all four exact Go board selectors were
staged at `ec892dc2899228e3a72526eb7d2f4cd5820445f3` on 2026-09-27. C1
reactivated at epoch 76 with five retained schedules and healthy services.
Worker 1 exposes the exact four `WORKABLE_GO_BOARD_IDS` below. Natural Go
success is still pending as of 14:51 UTC; parser parity alone is not live
execution evidence.

### Activation procedure used

Maintenance proof 36317322087 completed successfully before deploying candidate
#10089 using the supported c1 rollback and old exact **22-selector** cleanup
(`/tmp/jobseek-post-go-sitemap-selectors.py`). After successful image promotion,
use `/tmp/jobseek-post-go-maintenance-selectors.py stage NEW_FULL_SHA
https://kandou.bamboohr.com/careers/310` under the host mutation lock. The new
helper preserves the existing 22 entries and adds `WORKABLE_GO_BOARD_IDS`
with the four exact IDs above: **23 selectors total**. It checks the cold
receipt and exact release snapshot and writes the environment atomically.
Reactivate c1 through the supported script.

After that activation, subsequent cleanup must use the new 23-selector
helper, not the old one. Observe natural Go success, database URL parity,
and failure rates; never force due times. Keep the full #7966 gate open.

## Natural Go runs on v0.13.871

Without forced due times or duplicate requests, Pix4D completed Go monitoring
at **15:37:59 UTC** and Debiopharm at **15:45:58 UTC** on 2026-09-27. They
returned four and seven URLs respectively, with the exact hashes in the
comparison table. Both runs reported complete output, six requests and six
responses (2,698 and 3,316 response bytes). The subsequent database readback
matched every active URL hash and count, recorded the corresponding natural
success time, and showed zero consecutive failures for both enabled boards.

Unit8 then completed its natural Go run at **15:57:36 UTC**, returning all
14 URLs with the exact hash above, six requests/responses and 5,742 response
bytes. Database readback recorded success at 15:57:36.557 UTC, the same active
URL set, and zero failures. Hack The Box completed at **16:07:54 UTC**,
returning all 23 URLs with the exact comparison hash, six requests/responses
and 8,041 response bytes. Database success at 16:07:54.234 UTC retained the
same active URL set and zero failures. Thus all four selected boards have
actual natural Go output and database parity. The mode-0600 readback is
`/tmp/jobseek-workable-four-natural-db-20260927.jsonl`.
The production release is `ce5dfd821ca6e6b95d0b79a9eb534c26ddd486ec`, with
all four selectors restored at c1 epoch 78. This is actual output evidence for
all four boards, not a whole-lane resource comparison.

## Broader strict route: active on v0.13.873

The read-only census in `/tmp/jobseek-workable-route-census-20260927.jsonl`
found 77 enabled Workable boards. The existing strict `WORKABLE_GO_PERCENT=25`
selector admits 12; its union with the four explicit selectors is 15 boards.
All 12 had stable recent counts, active status and zero failures. At 100%,
the same eligibility rules would admit 52; this is coverage information, not
authorization to classify the other 25 as migrated. The initial expanded
readback for 15 boards is `/tmp/jobseek-workable-expanded-db-before.jsonl`.

The mode-0600 helper `/tmp/jobseek-post-go-registry-selectors.py`
preserves all previous 23 selectors and adds `WORKABLE_GO_PERCENT=25`. It
was staged after successful v0.13.873 deployment at full revision
`1f37e47ef036c1b08a5ca45dfca94cd9d7e3dbf6`, with c1 cold. Supported
reactivation reached epoch 80 with five ready schedules, zero inflight/dead
and all services healthy. The live worker route census confirms 15
`go-workable` boards. Subsequent natural output for the 11 newly admitted
boards is still pending. For the next deployment, finish supported c1
rollback, then clear all **24** selectors with this helper under the host
mutation lock against that full revision. The older 23-selector helper is
insufficient. Exact selector values are retained in
[the release evidence](evidence/go-registry-production-2026-09-27.json).
