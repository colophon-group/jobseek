# Candidate raw-record framing

This lane defines a version-neutral framing primitive only. It does not publish
`crawler.runtime/v1`, depend on protobuf, or expose a production runtime API.
Lane 6 may package this primitive after the complete contract stack is accepted.

Each record is a canonical unsigned base-128 little-endian `uint64` length,
followed by exactly that many payload bytes. Prefix byte 10 may contain only
`0x00` or `0x01` and may not continue. A prefix must use the shortest encoding.

The configured maximum includes both prefix and payload. Implementations check
the cap as:

```text
maximum < prefix_length OR payload_length > maximum - prefix_length
```

They perform that check before adding lengths, allocating payload storage,
slicing, or converting a hostile `uint64` length to a platform integer. This
also makes MaxUint64 and near-MaxUint64 prefixes ordinary typed failures instead
of overflow or panic cases.

## Exact buffers and streams

The exact-buffer API accepts exactly one record. An empty buffer is
`truncated_prefix`; `00` is a valid empty record; and `0000` is
`trailing_bytes`. `DecodeNext`/`decode_next` is the separate API for consuming
one record from a concatenated buffer. Python `decode_next` returns the
remainder as a `memoryview` over the original backing buffer, so repeatedly
decoding a concatenation does linear work instead of copying every suffix.
Exact decoding validates the record boundary and rejects trailing bytes before
copying the accepted payload.

The reader API reads one prefix byte at a time and only the declared payload;
it never reads ahead into the next record or calls a read-all helper. Clean EOF
before any prefix byte is distinct from `ambiguous_eof` inside a prefix or
payload. One call returns one record, so the caller controls backpressure by
deciding when to request another. Internal memory is bounded by one record that
has already passed the prefix-inclusive cap. A Go reader that returns zero
bytes and no error is rejected instead of spinning. Go treats
`io.ErrUnexpectedEOF` inside a started record like `io.EOF`; if the accompanying
bytes complete the record, that record still succeeds.

Shared failures use these stable codes:

| Code | Meaning |
| --- | --- |
| `nonminimal_prefix` | A value used more prefix bytes than necessary. |
| `prefix_overflow` | Byte 10 exceeded `0x01`, continued, or the prefix exceeded 10 bytes. |
| `frame_limit` | Prefix plus declared payload exceeded the configured maximum. |
| `truncated_prefix` | An exact buffer ended before its prefix completed. |
| `truncated_payload` | An exact buffer ended before its payload completed. |
| `trailing_bytes` | An exact buffer contained more than one record. |
| `ambiguous_eof` | A stream ended after any byte of a record. |
| `reader_contract` | A reader violated its progress or byte-count contract. |

Python additionally uses `invalid_maximum` and `invalid_buffer` for dynamic
type/range checks that Go's `uint64` and `[]byte` signatures provide statically.

## Shared corpus

Python and Go execute `fixtures/framing/vectors.json`. It covers empty, 127/128
and 16383/16384 boundaries, an exact maximum record, cap rejection, malformed
and nonminimal prefixes, byte-10 overflow/continuation, MaxUint64 and near-max
seeds, an exact-total-fit giant length with no body, truncated payloads,
small and 16 MiB trailing buffers, fragmented streams, ambiguous EOF, and
concatenated records.

`framing/generate_vectors.py` renders the JSON and its SHA-256 manifest with
stable ordering and formatting. `--check` verifies byte-for-byte determinism.
