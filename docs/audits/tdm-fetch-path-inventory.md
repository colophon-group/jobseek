# TDM fetch-path inventory

27 September 2026, static Python AST inventory; line numbers refer to this
follow-up branch. Candidates require control-flow review: a direct call alone
is not proof that a reservation check is absent or that a path is active.

Greenhouse and Ashby `discover()` now reject a reserved API response before
capturing or parsing its payload. Synthetic adapter-entry tests exercise this.
The shared parser accepts additional HTML attributes, ignores script/comment
literals, uses a 64 KiB bound and applies available HTML metadata before headers.
This follows [TDMRep section 6.7](https://w3c.github.io/cg-reports/tdmrep/CG-FINAL-tdmrep-20240510/).
Header-only early checks may conservatively stop before a later HTML opt-in.
Origin-file support and complete transport parity are not established.
The subsequent [stored-copy control implementation](tdm-stored-copy-controls.md)
adds durable observed restrictions and downstream checks; its production
rollout and the remaining discovery audit are tracked separately.

Go header checks are present in `apps/crawler/go/greenhouse-monitor/live.go`,
`lever-monitor/live.go`, `personio-monitor/live.go`, `successfactors-rss-monitor/live.go`
and the sitemap bounded HTTP client. Presence is not a completed live-runtime
parity test. Browser tests in `test_tdm.py` cover shared parsing/fetch hooks;
remaining active paths, rollout and retained-copy coverage stay in #10090.

| Direct fetch candidate | Lines |
| --- | --- |
| `apps/crawler/src/core/enrich/company.py` | 187, 581 |
| `apps/crawler/src/core/monitors/__init__.py` | 448 |
| `apps/crawler/src/core/monitors/_pcsx.py` | 376, 169 |
| `apps/crawler/src/core/monitors/accenture.py` | 82 |
| `apps/crawler/src/core/monitors/almacareer.py` | 410, 419, 473 |
| `apps/crawler/src/core/monitors/amazon.py` | 136, 267 |
| `apps/crawler/src/core/monitors/api_sniffer.py` | 2600 |
| `apps/crawler/src/core/monitors/ashby.py` | 275, 205, 227 |
| `apps/crawler/src/core/monitors/beehire.py` | 184 |
| `apps/crawler/src/core/monitors/bite.py` | 100, 136, 183 |
| `apps/crawler/src/core/monitors/brassring.py` | 474, 177 |
| `apps/crawler/src/core/monitors/breezy.py` | 156, 137, 225 |
| `apps/crawler/src/core/monitors/comeet.py` | 273 |
| `apps/crawler/src/core/monitors/deel.py` | 156, 111, 194 |
| `apps/crawler/src/core/monitors/dvinci.py` | 187, 134 |
| `apps/crawler/src/core/monitors/fenbi.py` | 332, 337 |
| `apps/crawler/src/core/monitors/gem.py` | 131, 155 |
| `apps/crawler/src/core/monitors/greenhouse.py` | 282, 211, 233 |
| `apps/crawler/src/core/monitors/headhunter.py` | 406, 271, 505 |
| `apps/crawler/src/core/monitors/hibob.py` | 145 |
| `apps/crawler/src/core/monitors/hirehive.py` | 248 |
| `apps/crawler/src/core/monitors/hireology.py` | 136 |
| `apps/crawler/src/core/monitors/infor.py` | 241 |
| `apps/crawler/src/core/monitors/jarvi.py` | 244 |
| `apps/crawler/src/core/monitors/jobstreet.py` | 119 |
| `apps/crawler/src/core/monitors/jobylon.py` | 524 |
| `apps/crawler/src/core/monitors/kipt.py` | 226 |
| `apps/crawler/src/core/monitors/lever.py` | 205, 234 |
| `apps/crawler/src/core/monitors/manatal.py` | 71 |
| `apps/crawler/src/core/monitors/mokahr.py` | 157, 658 |
| `apps/crawler/src/core/monitors/nextdata.py` | 1592 |
| `apps/crawler/src/core/monitors/nowhiring.py` | 65, 113, 129 |
| `apps/crawler/src/core/monitors/oracle_hcm.py` | 83 |
| `apps/crawler/src/core/monitors/peoplesoft.py` | 318 |
| `apps/crawler/src/core/monitors/personio.py` | 281, 421 |
| `apps/crawler/src/core/monitors/pinpoint.py` | 173, 200 |
| `apps/crawler/src/core/monitors/raw.py` | 23, 45 |
| `apps/crawler/src/core/monitors/recruitee.py` | 284, 184 |
| `apps/crawler/src/core/monitors/recruiter_co_kr.py` | 311, 551, 381 |
| `apps/crawler/src/core/monitors/rippling.py` | 75, 95 |
| `apps/crawler/src/core/monitors/seamlesshiring.py` | 74 |
| `apps/crawler/src/core/monitors/sitemap.py` | 146, 337 |
| `apps/crawler/src/core/monitors/smartrecruiters.py` | 1070, 1097, 1132, 1137, 1183 |
| `apps/crawler/src/core/monitors/softgarden.py` | 102, 127 |
| `apps/crawler/src/core/monitors/talentreef.py` | 77, 160 |
| `apps/crawler/src/core/monitors/traffit.py` | 198, 264 |
| `apps/crawler/src/core/monitors/umantis.py` | 1123 |
| `apps/crawler/src/core/monitors/universia.py` | 78 |
| `apps/crawler/src/core/monitors/welcometothejungle.py` | 79, 106, 254 |
| `apps/crawler/src/core/monitors/workable.py` | 318, 344 |
| `apps/crawler/src/core/monitors/workday.py` | 1044, 1384 |
| `apps/crawler/src/core/monitors/ycombinator.py` | 61 |
| `apps/crawler/src/core/scrape.py` | 26 |
| `apps/crawler/src/core/scrapers/bite.py` | 144 |
| `apps/crawler/src/core/scrapers/eightfold.py` | 129, 196 |
| `apps/crawler/src/core/scrapers/embedded.py` | 388 |
| `apps/crawler/src/core/scrapers/headhunter.py` | 66, 72 |
| `apps/crawler/src/core/scrapers/infor.py` | 113 |
| `apps/crawler/src/core/scrapers/jobconvo.py` | 138 |
| `apps/crawler/src/core/scrapers/jsonld.py` | 451 |
| `apps/crawler/src/core/scrapers/mokahr.py` | 192 |
| `apps/crawler/src/core/scrapers/onlyfy.py` | 191, 199, 210 |
| `apps/crawler/src/core/scrapers/paycor.py` | 73 |
| `apps/crawler/src/core/scrapers/paylocity.py` | 105 |
| `apps/crawler/src/core/scrapers/pdf.py` | 430 |
| `apps/crawler/src/core/scrapers/recruiterbox.py` | 95 |
| `apps/crawler/src/core/scrapers/rippling.py` | 153 |
| `apps/crawler/src/core/scrapers/smartrecruiters.py` | 150 |
| `apps/crawler/src/core/scrapers/workable.py` | 252, 255 |
| `apps/crawler/src/core/scrapers/workday.py` | 369 |
