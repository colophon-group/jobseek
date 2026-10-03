"""Capture pinned httpx content decoding and raw-byte accounting behavior."""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import zlib
from pathlib import Path

import httpx

PAYLOAD = b'{"jobs":[{"absolute_url":"https://example.com/jobs/1","title":"Engineer"}]}'
GZIP = gzip.compress(PAYLOAD, mtime=0)
DEFLATE = zlib.compress(PAYLOAD)
compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
RAW_DEFLATE = compressor.compress(PAYLOAD) + compressor.flush()

cases = [
    ("identity", "", PAYLOAD),
    ("gzip", "gzip", GZIP),
    ("deflate", "deflate", DEFLATE),
    ("raw-deflate", "deflate", RAW_DEFLATE),
    ("gzip-empty", "gzip", b""),
    ("deflate-empty", "deflate", b""),
    ("gzip-two-members", "gzip", GZIP + gzip.compress(b"discard", mtime=0)),
    ("gzip-trailing-bytes", "gzip", GZIP + b"discard"),
    ("deflate-trailing-bytes", "deflate", DEFLATE + b"discard"),
    ("gzip-missing-footer", "gzip", GZIP[:-8]),
    ("deflate-missing-footer", "deflate", DEFLATE[:-4]),
    ("raw-deflate-truncated", "deflate", RAW_DEFLATE[:-1]),
    ("gzip-deflate-chain", "gzip, deflate", zlib.compress(GZIP)),
    ("deflate-gzip-chain", "deflate, gzip", gzip.compress(DEFLATE, mtime=0)),
    ("invalid-gzip-header", "gzip", b"not gzip"),
    ("gzip-short-valid-prefix", "gzip", b"\x1f"),
    ("gzip-short-invalid-prefix", "gzip", b"x"),
    ("gzip-short-bad-magic", "gzip", b"ab"),
    ("gzip-short-bad-method", "gzip", b"\x1f\x8b\x00"),
    ("gzip-complete-bad-method", "gzip", b"\x1f\x8b\x00\x00"),
    ("gzip-reserved-flags", "gzip", b"\x1f\x8b\x08\xe0"),
    ("invalid-gzip-checksum", "gzip", GZIP[:-8] + b"\xff" * 8),
    ("invalid-deflate-checksum", "deflate", DEFLATE[:-4] + b"\xff" * 4),
    ("unknown-encoding-is-identity", "unknown", PAYLOAD),
    ("mixed-case-gzip", "GZip", GZIP),
]


async def capture(name, encoding, body):
    row = {
        "name": name,
        "encoding": encoding,
        "body_base64": base64.b64encode(body).decode(),
        "encoded_bytes": len(body),
    }

    def handle(request):
        return httpx.Response(200, headers={"content-encoding": encoding}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        try:
            response = await client.get("https://example.com/jobs")
        except httpx.DecodingError:
            row.update(kind="body_failed", decoded_base64="")
        else:
            row["decoded_base64"] = base64.b64encode(response.content).decode()
            try:
                response.json()
            except (ValueError, UnicodeError):
                row["kind"] = "invalid_inventory"
            else:
                row["kind"] = ""
    return row


async def main():
    rows = [await capture(*case) for case in cases]
    Path(__file__).with_name("python_http_decoding.json").write_text(
        json.dumps(rows, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
