# EU occupation locale recon for #3356

Date: 2026-07-07

Scope: read-only production Postgres probe of active postings in the target EU
countries from #3356. The scan counted postings whose `location_ids` include
the country id and whose `occupation_id` is currently NULL. Local-language
engineering/data title signals were matched against the `titles` text array.

| Locale | Country | Active postings | NULL occupation | NULL + local signal |
| --- | --- | ---: | ---: | ---: |
| pl | Poland | 1,295 | 729 | 36 |
| es | Spain | 1,105 | 550 | 2 |
| nl | Netherlands | 1,032 | 666 | 0 |
| pt | Portugal | 633 | 311 | 6 |
| cs | Czechia | 452 | 191 | 2 |
| sv | Sweden | 280 | 123 | 1 |
| hu | Hungary | 248 | 124 | 2 |
| ro | Romania | 426 | 188 | 2 |
| bg | Bulgaria | 326 | 117 | 0 |
| el | Greece | 255 | 189 | 0 |
| da | Denmark | 239 | 152 | 0 |
| fi | Finland | 101 | 64 | 0 |
| hr | Croatia | 83 | 38 | 0 |
| sk | Slovakia | 170 | 83 | 0 |
| sl | Slovenia | 42 | 13 | 0 |
| lt | Lithuania | 99 | 29 | 0 |
| lv | Latvia | 39 | 15 | 0 |
| et | Estonia | 124 | 50 | 0 |

Representative missed active titles:

- `Inzynier Jakosci` / `Inżynierka / Inżynier Nadzoru Jakości` in Poland.
- `Desarrollador/a Senior Salesforce_Platform Event` in Spain.
- `Engenheiro de Qualidade Cliente` in Portugal.
- `Projektový inženýr mechanik (m/ž)` and `PLC Programátor (m/ž)` in Czechia.
- `Serviceingenjör datortomografi` in Sweden.
- `Karbantartó Mérnök` in Hungary.
- `Inginer grupuri electrogene si DSI - telecom & data centers` in Romania.

The sample is not purely software engineering: several common misses are
quality, automation, mechanical, maintenance, electrical, or network/telecom
roles. The fix therefore expands locale display-name support and adds targeted
native aliases to existing matching slugs rather than mapping every native
word for "engineer" to `software-engineer`.

After applying the local resolver changes and rechecking the same active NULL
target-country set read-only, the patched matcher would classify 52 current
rows: Poland 27, Portugal 6, Czechia 8, Hungary 3, Romania 2, Spain 2, and one
each in Netherlands, Finland, Croatia, and Slovakia. Representative matches
include `Inzynier Jakosci -> quality-manager`,
`Desarrollador/a Senior Salesforce_Platform Event -> software-engineer`,
`PLC Programátor -> automation-engineer`,
`Karbantartó Mérnök -> maintenance-technician`, and
`Inginer grupuri electrogene si DSI -> electrical-engineer`.

Post-merge note: this change improves new/updated posting resolution during
crawler processing. Existing active rows with `occupation_id IS NULL` need a
separate `crawler reprocess-occupations --include-nulls` operator run after
deployment if immediate historical coverage is desired.
