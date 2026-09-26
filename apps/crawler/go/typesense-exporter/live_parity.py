"""Read-only same-row Python/Go Typesense projection comparison.

Run inside the crawler image with its normal database environment, after
placing the Go binary at ``/usr/local/bin/go-typesense-exporter``. It reads
one recent local PostgreSQL batch and never contacts Typesense or changes the
export cursor. Output contains counts and field names, not posting payloads.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from datetime import datetime, timedelta

import asyncpg

from src.exporter import PostingSchema, TaxonomyMaps, _build_typesense_docs


async def compare() -> None:
    binary = os.environ.get("GO_TYPESENSE_EXPORTER_BINARY", "/usr/local/bin/go-typesense-exporter")
    pool = await asyncpg.create_pool(os.environ["LOCAL_DATABASE_URL"], min_size=2, max_size=8)
    try:
        maps = TaxonomyMaps()
        await maps.refresh(pool)
        try:
            process = subprocess.run(
                [binary, "--shadow-batch"],
                env={**os.environ, "GO_TYPESENSE_SHADOW_DOCS": "1"},
                capture_output=True,
                check=True,
                timeout=120,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Go shadow read failed: {exc.stderr[:1000]!r}") from exc
        go_result = json.loads(process.stdout)
        cutoff = datetime.fromisoformat(go_result["cutoff"])
        rows = await pool.fetch(
            PostingSchema.select_typesense_changed_sql("last_seen_at", "updated_at"),
            cutoff - timedelta(hours=6),
            uuid.UUID(int=0),
            200,
            cutoff,
        )
        if not rows:
            raise RuntimeError("no recent posting rows available for live projection parity")
        expected = _build_typesense_docs([dict(row) for row in rows], maps)
        actual = go_result["documents"]
        if len(actual) != len(expected) or go_result["rows"] != len(expected):
            raise RuntimeError("Go and Python shadow batch sizes differ")
        mismatches: dict[str, int] = {}
        for python_doc, go_doc in zip(expected, actual, strict=True):
            for field in set(python_doc) | set(go_doc):
                if python_doc.get(field) != go_doc.get(field):
                    mismatches[field] = mismatches.get(field, 0) + 1
        print(
            json.dumps(
                {
                    "rows": len(rows),
                    "mismatched_fields": mismatches,
                    "go_document_sha256": go_result["document_sha256"],
                },
                sort_keys=True,
            )
        )
        if mismatches:
            raise SystemExit(1)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(compare())
