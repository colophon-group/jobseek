# Candidate runtime v1 projected effects

This document defines the target-bound offline projection used by
`semantics.md`. `ProjectedEffects` is the existing candidate message in
`runtime.proto`. It consumes the dormant optional source-identity fields
landed by the reviewed IDL amendment; this lane adds no wire field, persistence
policy, eligibility flag, browser action, or gone mutation.

## Subject input shapes

After the common preconditions in `semantics.md` pass, a scrape input contains
exactly:

```text
request := {
  source_url,
  request_id?,
  origin_request_id?
}
result := {content}
```

`source_url` is required and canonicalized. A projected result also requires
nonempty `request_id` and `origin_request_id`; their syntactic optionality lets
a safely stopped corpus input omit irrelevant identity. `content` is the
closed job object from `semantics.md`.

A monitor request has the same identity rule and exactly `target_url`,
`request_id?`, and `origin_request_id?`. It supplies exactly one of a single
`result` or a nonempty ordered `batches` array. Each result is exactly:

```text
{
  urls: [string, ...],
  jobs: [{url, content, source_identity?}, ...],
  filtered_count: uint64,
  security_filtered_count: uint64,
  hybrid: boolean,
  truncated: boolean,
  new_sitemap_url?: string,
  metadata_updates?: object
}
```

When `metadata_updates` is present it must be an object. JSON null is not
absence and rejects as `invalid_projection`.

Each discovered job is exactly `{url, content}` plus optional
`source_identity`. Omission or JSON null retains legacy URL identity. A
non-null identity must match
`[a-z][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9._-]{0,63}:[A-Za-z0-9][A-Za-z0-9._~:/-]{0,383}`
as a whole string; malformed values reject as `invalid_projection`. This is
the same closed JSON surface as the reviewed IDL amendment. Each batch is exactly
`{checked_count: uint64, complete: boolean, result}`. Every batch must have
`complete=true`. The checked counts are summed only to enforce uint64 safety;
they do not create a `ProjectedEffects` field. A false batch completeness flag
suppresses as `ineligible_history` and any count or aggregate overflow
suppresses as `counter_overflow`.

A browser subject with exactly `result: {browser_result}` is well formed but
suppresses as `unsupported_result`; browser projection is deferred. An unknown
subject kind rejects as `unsupported_result`. Common privacy/history failures
take precedence and stop before these subject shapes are inspected.

The optional `existing_projected_effects` alignment probe requires the four
array members `urls_to_upsert`, `content_hashes`, `job_effects`, and `targets`.
Other existing wire members do not participate in this probe. The four arrays
must have equal length. At every index, the canonical URL must equal
`job_effects[i].source_url` and `targets[i].url`; the string hash must equal
`job_effects[i].content_sha256` and `targets[i].content_sha256`, with an absent
target hash interpreted as the empty sentinel. Any inconsistency is
`invalid_projection`. Each probed job effect is exactly
`{source_url, content_sha256}` plus an optional, non-null, valid
`source_identity`; the probe does not infer an identity from another field.

## Atomic scrape projection

Canonicalize `request.source_url`, canonicalize and visibility-check the whole
job, and compute the target-bound content hash. The canonical source URL is
both `target_url` and the sole upsert identity. No record ID is invented.

The one complete tuple is expanded in lockstep to:

```text
urls_to_upsert[0] = canonical source URL
content_hashes[0] = content SHA-256
job_effects[0] = {source_url, content_sha256}
targets[0] = {
  url,
  action: "PROJECTED_ACTION_UPSERT",
  content_sha256
}
```

`execution_kind` is `EXECUTION_KIND_SCRAPE`; both filtered counts are zero;
`hybrid`, `truncated`, and `gone_detection_allowed` are false. An invalid
description, URL, locale, JSON value, or target binding suppresses or rejects
the entire scrape according to the closed reason mapping; no partial scrape
projection exists.

Scrape input has no stable provider-identity field. Its job effect therefore
omits `source_identity` and preserves the legacy canonical-URL logical key.

## Atomic monitor projection

Process result batches in source order. Across all batches:

- sum `filtered_count` and `security_filtered_count` with uint64 overflow
  checks;
- OR `hybrid` and `truncated`;
- collect URL-only observations and rich discovered jobs;
- retain metadata update presence and order; and
- canonicalize every present sitemap URL and require all present canonical
  sitemap values to agree.

Different sitemap values suppress as `canonical_collision`. If exactly one
result supplies metadata, hash that metadata object directly. For multiple
results, when any metadata is present, hash
`{"batches":[m0,m1,...]}` where each `mi` is that batch's metadata object or
JSON null when absent. Batch order, metadata members, extension/payload order,
and absence positions are lossless. If no result supplies metadata, omit
`metadata_updates_sha256`. Omit `new_sitemap_url` when every result omits it.

Build complete logical records before sorting. A rich job with a non-null
`source_identity` is keyed in the explicit-identity domain. An omitted or null
identity is keyed in the separate legacy canonical-URL domain. A URL-only
observation does not declare a logical identity: when it has the same canonical
URL and source spelling as a rich job it is absorbed into that rich record;
otherwise it becomes a legacy URL-only record.

Byte-identical rich duplicates with the same logical key deduplicate. When one
explicit identity appears at multiple canonical URLs with byte-identical
canonical content, retain exactly one record and choose the lexicographically
smallest canonical URL by UTF-8 bytes as its deterministic outbound URL. This
winner rule is independent of input and batch order. Divergent content for one
explicit identity suppresses as `canonical_collision`; without provider alias
knowledge this lane does not guess which content belongs to the durable key.

Every canonical URL claimed by a rich job must have exactly one logical key.
Two explicit identities, or explicit and legacy rich identities, claiming one
canonical URL suppress as `canonical_collision`. A different source spelling
that canonicalizes to an already observed URL also suppresses, even if the
logical key agrees. Explicit and legacy rich records at distinct canonical URLs
remain distinct; no alias relationship is invented between them.

Sort complete records first by logical-key domain (explicit before legacy),
then by the explicit identity or canonical legacy URL in UTF-8 byte order. From
that one order derive `targets`, `job_effects`, `urls_to_upsert`, and
`content_hashes`. `source_url` and `urls_to_upsert` always carry the selected
outbound publication URL. A rich explicit job effect additionally carries the
unchanged `source_identity`, which is its durable logical key.
Rich tuples carry their target-bound content hash everywhere. A URL-only tuple
uses the empty string only in `content_hashes` and
`job_effects.content_sha256`; its `ProjectedTarget.content_sha256` is absent.
The empty sentinel is forbidden for rich content. Sorting a key independently
of its value is invalid.

`execution_kind` is `EXECUTION_KIND_MONITOR` and `target_url` is the canonical
monitor request URL. `filtered_count`, `security_filtered_count`, `hybrid`, and
`truncated` are their aggregates. Safe targets remain projectable when any
gone condition fails.

## Gone rule

`gone_detection_allowed=true` only for a protocol-complete monitor whose batch
history is complete, every included job is valid, `hybrid=false`,
`truncated=false`, `filtered_count=0`, and `security_filtered_count=0`.
Otherwise a complete monitor may retain safe offline upserts with
`gone_detection_allowed=false`. Incomplete history or a batch with
`complete=false` suppresses the whole result.

Provider or permanent-gone state never creates a target or authorizes a
mutation in this lane. The only projected target action is
`PROJECTED_ACTION_UPSERT`.

## Closed `ProjectedEffects` JSON form

Every projected object contains exactly these required members:

```text
{
  urls_to_upsert,
  content_hashes,
  gone_detection_allowed,
  hybrid,
  truncated,
  filtered_count,
  security_filtered_count,
  job_effects,
  request_id,
  origin_request_id,
  execution_kind,
  target_url,
  targets,
  canonicalization_rule: "CANONICALIZATION_RULE_RUNTIME_V1",
  content_hash_rule: "HASH_RULE_CONTENT_SHA256_V1"
}
```

`new_sitemap_url` and `metadata_updates_sha256` are the only optional members.
Each `job_effect` is exactly `{source_url, content_sha256}` plus optional
`source_identity` for an explicit rich logical key. Each target is exactly
`{url, action}` plus `content_sha256` for rich content. The four target
arrays have equal length and remain index-aligned. Counts are uint64; hashes
are lowercase 64-character hex except for the documented URL-only legacy
sentinel.

This projection is comparison evidence only. It does not write authoritative
state and grants no persistence, gone, runtime, packaging, deployment, or
activation authority, even when its semantic digest matches. It has no `ws`,
Murmur, MCP, browser-runtime, DB, Redis, queue, or provider integration.
[Lane 6](https://github.com/colophon-group/jobseek/issues/8046) exclusively
owns generation, packaging, activation, and release-policy revocation.
