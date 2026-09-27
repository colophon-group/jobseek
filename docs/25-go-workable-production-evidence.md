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

## Pending exact-board activation

Wait for maintenance proof 36317322087 to finish, then deploy candidate
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
