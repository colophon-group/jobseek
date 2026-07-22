#!/usr/bin/env bash
# Verify the crawler's real private PostgreSQL and Typesense paths without printing endpoints.
set -euo pipefail

POSTGRES_PRIVATE_IP="${JOBSEEK_POSTGRES_PRIVATE_IP:-}"
TYPESENSE_PRIVATE_IP="${JOBSEEK_TYPESENSE_PRIVATE_IP:-}"
for value in "$POSTGRES_PRIVATE_IP" "$TYPESENSE_PRIVATE_IP"; do
  [[ "$value" =~ ^10\.|^192\.168\.|^172\.(1[6-9]|2[0-9]|3[01])\. ]] || {
    echo "A private service IPv4 value is required" >&2
    exit 1
  }
done

mapfile -t exporters < <(
  docker ps \
    --filter label=com.docker.compose.service=exporter \
    --format '{{.ID}}'
)
[[ "${#exporters[@]}" -eq 1 ]] || {
  echo "Expected exactly one live exporter container" >&2
  exit 1
}

docker exec \
  -e JOBSEEK_EXPECTED_POSTGRES_PRIVATE_IP="$POSTGRES_PRIVATE_IP" \
  -e JOBSEEK_EXPECTED_TYPESENSE_PRIVATE_IP="$TYPESENSE_PRIVATE_IP" \
  "${exporters[0]}" \
  uv run --no-sync python -c '
import asyncio
import json
import os
import urllib.parse
import urllib.request

import asyncpg

from src.config import settings


async def verify_postgresql():
    expected = os.environ["JOBSEEK_EXPECTED_POSTGRES_PRIVATE_IP"]
    parsed = urllib.parse.urlsplit(settings.local_database_url)
    if parsed.hostname != expected:
        raise SystemExit("crawler PostgreSQL DSN does not use the expected private host")
    connection = await asyncpg.connect(settings.local_database_url, timeout=10)
    try:
        if await connection.fetchval("select 1") != 1:
            raise SystemExit("PostgreSQL private-path query returned an unexpected value")
    finally:
        await connection.close()


asyncio.run(verify_postgresql())

expected_typesense = os.environ["JOBSEEK_EXPECTED_TYPESENSE_PRIVATE_IP"]
if settings.typesense_host != expected_typesense or settings.typesense_protocol != "http":
    raise SystemExit("crawler Typesense configuration does not use the expected private host")
url = f"http://{settings.typesense_host}:{settings.typesense_port}/health"
with urllib.request.urlopen(url, timeout=10) as response:
    payload = json.load(response)
if payload.get("ok") is not True:
    raise SystemExit("Typesense private-path health check failed")
'

echo "Crawler private PostgreSQL query and Typesense health checks passed"
