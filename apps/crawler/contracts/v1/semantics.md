# Candidate runtime v1 semantic normalization

This document defines a deterministic offline candidate operation over
synthetic projections that have passed the execution protocol in
`execution-protocol.md` and the privacy transform in `redaction.md`. It does
not authorize runtime consumption, persistence, gone mutation, scheduling,
packaging, deployment, or activation. [Lane 6](https://github.com/colophon-group/jobseek/issues/8046)
alone may activate `crawler.runtime/v1`.

## Closed case and result forms

The shared manifest is exactly:

```text
{format, required_case_ids, cases}
```

`format` is `jobseek.runtime.semantics-corpus/v1`. `required_case_ids` and
`cases` contain the same 50 independently hard-coded IDs in the same order.
Each case is exactly `{id, subject_kind, input, expected}`; `id` is a nonempty
string and `subject_kind` is `scrape`, `monitor`, `browser`, or an unknown
string used to prove rejection. All corpus values are synthetic.

An input has required `preconditions` and may contain only `request`, `result`,
`batches`, `existing_projected_effects`, `comparison_content`,
`comparison_content_sha256`, and `comparison_metadata_updates`. The three
`comparison_*` members are conformance evidence and do not affect projection.
`result` and `batches` are mutually exclusive for a monitor. Subject-specific
forms are closed in `projection.md`.

`preconditions` is exactly:

```text
{
  protocol_accepted: boolean,
  terminal_status: string,
  eligible_for_commit: boolean,
  batches_complete: boolean,
  privacy_status: "unchanged" | "transformed" | "rejected"
}
```

Projection requires `protocol_accepted=true`, `terminal_status="success"`,
`eligible_for_commit=true`, `batches_complete=true`, and privacy status
`unchanged` or `transformed`. `privacy_status="rejected"` suppresses before
subject data is inspected, but only after all five fields pass their declared
type checks. Any malformed precondition type rejects as `invalid_projection`.
Any other failed protocol/history precondition
suppresses as `ineligible_history`; an unknown privacy status rejects as
`invalid_projection`. Protocol acceptance and privacy transformation are
preconditions, not operations reimplemented by this lane.

The result union is closed:

```text
projected  := {case_id, status: "projected", projected_effects, semantic_sha256}
suppressed := {case_id, status: "suppressed", reason}
rejected   := {case_id, status: "rejected", reason}
```

Suppressed and rejected results contain no projection, hash, raw-derived
detail, snippet, offset, diagnostic, or input member. Rejection is reserved
for a malformed candidate shape or unknown subject. A well-formed but unsafe,
ineligible, browser, or otherwise unsupported result is suppressed.

The reason registry is exactly:

| Reason | Meaning |
| --- | --- |
| `invalid_visible_content` | A supplied description violates the closed visibility profile or no supplied description is visible. |
| `invalid_url` | A URL violates the closed URL profile. |
| `invalid_locale` | A language or localization key is outside the locale profile. |
| `canonical_collision` | Distinct source identities or divergent values collapse to one canonical identity. |
| `counter_overflow` | A count, aggregate count, or length exceeds unsigned 64-bit range. |
| `invalid_projection` | A candidate shape, JSON value, or aligned effect is malformed or internally inconsistent. |
| `ineligible_history` | Protocol, terminal, eligibility, batch-history, or completeness prerequisites fail. |
| `privacy_rejected` | Lane 4 rejected the input. |
| `unsupported_result` | A browser result is deferred or the subject kind is unknown. |

No other reason or free-text diagnostic is permitted.

## Visible description profile

A canonical job is content-eligible only if `description_html` or at least one
localized `description_html` contains a visible Unicode scalar. A title alone
is insufficient. Every supplied description must be valid under this profile;
an invalid description suppresses its whole scrape or monitor result.

Tokenization is dependency-free and bounded. The UTF-8 input limit is
1,048,576 bytes inclusive and element nesting is at most 128. No DOM, regular
expression, Unicode normalization, or general entity decoder participates.
Tag and attribute names and the recognized attribute values are
ASCII-case-insensitive; quoted and unquoted attributes are accepted. Comments
and complete `script`, `style`, `template`, and `noscript` subtrees are ignored.
A complete element subtree is also ignored when it has a `hidden` attribute,
`aria-hidden=true`, or an inline declaration equal after ASCII-whitespace trim
and ASCII case-folding to `display:none`, `visibility:hidden`, or
`visibility:collapse`.

After all other tags are removed, only semicolon-terminated `&nbsp;`, a
semicolon-terminated decimal reference to U+00A0, and a semicolon-terminated
hexadecimal reference to U+00A0 are decoded as non-breaking space. Other
entity text, including semicolon-less forms, remains literal and is visible when it
contains a visible scalar. The non-visible set is exactly U+0009–U+000D,
U+0020, U+0085, U+00A0, U+1680, U+2000–U+200B, U+2028, U+2029, U+202F,
U+205F, U+3000, and U+FEFF. Any other valid scalar is visible.

NUL; C0 controls other than U+0009–U+000D; U+007F–U+009F other than U+0085;
an unclosed comment or quoted tag; a tag without a closing `>`; ambiguous
hidden-subtree nesting; an unclosed ignored or hidden subtree; excess bytes;
or excess nesting suppresses as `invalid_visible_content`. An unclosed
ordinary visible element is not by itself a visibility failure.

## URL profile

A canonical URL is an absolute `http` or `https` URL. Reject `invalid_url` for
an empty value; any Unicode whitespace or control scalar; backslash; missing
host; userinfo; malformed percent escape; port zero or a port above 65535;
non-ASCII, percent-encoded, trailing-dot, or invalid DNS hosts; and IPv4,
IPv6, or legacy numeric IP spellings. Legacy numeric parsing follows the
closed inet_aton 1–4 component form: each component is decimal, leading-zero
octal, or `0x` hexadecimal, with 8/24, 8/8/16, or 8/8/8/8 bit allocation for
2, 3, or 4 components and a 32-bit limit for one component. A numeric-looking
spelling that does not parse within those rules may remain an ASCII DNS name.
`localhost` is allowed. Other hosts are
ASCII DNS names of at most 253 bytes whose nonempty labels are at most 63 bytes,
contain only ASCII letters, digits, or hyphen, and do not start or end with
hyphen.

Lowercase scheme and host. Remove port 80 from HTTP and port 443 from HTTPS by
numeric value, discard the fragment after validating its escapes, and replace
an empty path with `/`. Percent-decode only unreserved ASCII, uppercase the hex
digits of all remaining escapes, and UTF-8-percent-encode non-ASCII path and
query scalars. Preserve only RFC 3986 ASCII characters legal in the component.
Resolve decoded `.` and `..` path segments without collapsing empty segments
or repeated slashes; traversal above root is invalid.

Split a nonempty query only on `&`; `+` is literal. Preserve duplicates and
the distinction between `a` and `a=`. Stable-sort fields by canonical key
bytes, canonical value bytes, `has_equals` (`false` first), then original
ordinal. Rejoin with `&`; an empty query is absent. Canonical comparison and
sorting use UTF-8 bytes. Distinct source URL spellings that collapse to one
monitor target are a `canonical_collision`; byte-identical duplicates may
deduplicate under the projection rules.

## Locale and field normalization

Replace `_` with `-`, ASCII-lowercase for lookup, and emit exactly one of:

```text
de de-CH de-DE en en-CH en-GB en-US fr fr-CH fr-FR it it-CH it-IT
```

Every other language or localization spelling is `invalid_locale`.
Localizations are keyed and sorted by canonical locale. Duplicate entries
deduplicate only when their source locale spelling and canonical object are
byte-identical; aliases or divergent objects collapsing to one locale are a
`canonical_collision`.

`skills` and `locations.values` are string sets: sort by UTF-8 bytes and remove
only byte-identical duplicates. Monitor URLs and jobs keyed by canonical URL
are set-like as specified in `projection.md`. Localizations are always emitted
as a canonical array, with absence represented as an empty array. Other
optional members preserve absent versus present-empty, including absent
`locations` versus present `{"values":[]}`. Extensions, metadata, and every
array not declared set-like preserve input order. Do not trim strings or apply
Unicode normalization.

A job object may contain only `title`, `description_html`, `locations`,
`employment_type`, `job_location_type`, `date_posted`, `base_salary`,
`language`, `localizations`, `skills`, and `extensions`. A localization may
contain only required `locale` and optional `title` and `description_html`.
When localized `description_html` is present it must be a string; JSON null is
not absence and rejects as `invalid_projection`.
`locations`, when present, is exactly `{values: [string, ...]}`. Values not
normalized above remain the already protocol-valid, lane-4-safe JSON values
supplied by the candidate input.

## Canonical bytes and hashes

Canonical JSON is UTF-8 JSON over null, booleans, schema-valid integers, valid
Unicode-scalar strings, arrays, and string-keyed objects. Recursively sort
object keys by Unicode scalar sequence. Preserve array order except for the
field rules above. Emit integers in minimal base 10, valid non-ASCII literally,
and no insignificant whitespace or trailing newline. Escape only quote,
backslash, and U+0000–U+001F, using the shortest JSON named escape when one
exists and lowercase `\u00xx` otherwise. Do not escape slash, U+2028, or
U+2029. Floats, surrogates, invalid UTF-8, and unrepresentable values reject as
`invalid_projection`.

For bytes `x`, `L(x)` is the unsigned 64-bit big-endian byte length followed by
`x`. A length above 2^64−1 suppresses as `counter_overflow`. All digests are
lowercase 64-character SHA-256 hex:

```text
content_sha256 = SHA256(
  "jobseek.runtime.v1.content-sha256\0" ||
  L(canonical_url_utf8) || L(canonical_job_json)
)

metadata_updates_sha256 = SHA256(
  "jobseek.runtime.v1.metadata-sha256\0" ||
  L(canonical_target_url_utf8) || L(canonical_metadata_json)
)

semantic_sha256 = SHA256(
  "jobseek.runtime.v1.semantic-sha256\0" ||
  L(canonical_projected_result_json_without_semantic_sha256)
)
```

The URL length prefix binds every content or metadata value to its target.
Suppressed and rejected inputs are neither included in a digest nor exposed in
their result. Murmur run/body hashes are a separate domain and are never
substituted for these hashes.

## Conformance boundary

The manifest, checker, and Python/Go assertions are pure, deterministic,
standard-library, and zero-network. Both languages hard-code all 50 required
IDs and compare complete result objects, ordered arrays, aligned effects, and
all digest bytes. This candidate surface does not integrate with `ws`, Murmur,
MCP, a crawler runtime, an artifact resolver, a database, Redis, a queue, or a
deployment path.
