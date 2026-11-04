# Job enrichment

Production monitor and detail processing use the shared
[Go job-enrichment module](../apps/crawler/go/job-enrichment/README.md).
Go workers call its matcher and location index in process. It owns occupation,
seniority, technology and experience matching, salary extraction, language
recognition and description HTML normalization; the caller resolves database
identities and persists canonical output. See the
[field reference](08-job-data-fields.md) and completed
[delivery checkpoint](27-go-lightpanda-continuation-plan.md).

## Retained offline tools

Company setup still uses the Python metadata helpers under
`apps/crawler/src/core/enrich/` for JSON-LD and Wikidata evidence. The Python
job-processing interfaces and Go bridge remain useful workspace tools and
independent parity oracles. They do not provide a production runtime fallback.

The separate [labelled-posting routine](15-data-sampling-routine.md) creates
structured training/evaluation records through its normalizer, splitter and
extractors. Follow its Codex subscription workflow and validation gates; it is
not a bulk production enrichment daemon.

## Retired provider-batch implementation

The unused Python provider-batch subsystem, provider SDK dependency extra,
configuration keys and smoke instructions were retired in repository cleanup.
There is no supported `enricher` command, provider polling daemon or
`ENRICH_PROVIDER` activation path. Do not introduce provider API work through
those old instructions.

The former design and extracted-field catalogue remain in
[immutable source history](https://github.com/colophon-group/jobseek/blob/6fa1d1125327ccb4d79f4c20b72124c76d1d060a/docs/09-enrichment.md).
This source cleanup does not drop or rewrite stored `job_posting.enrichment`
values, the historical `enrich_batch` table or migration files. Removing
persisted schema requires a separate database migration decision.

## Verification

Use the shared module's Go regression/parity tests and the production native
worker tests for processing changes. Keep the current complete-lane comparison
for canonical output and resource qualification. Run the labeller's schema and
agent-contract checks for labelling changes. Never substitute a provider-batch
smoke for either surface.
