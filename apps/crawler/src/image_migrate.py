"""One-time migration: download existing company images and upload to R2.

For each company in companies.csv with non-empty logo_url/icon_url:
1. Download the image (preserving original format)
2. Upload to R2 at companies/<slug>/logo.<ext> or icon.<ext>
3. Update CSV with R2 public URLs

Usage:
    uv run python -m src.image_migrate
    uv run python -m src.image_migrate --dry-run
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import httpx

from src.shared.constants import DATA_DIR

CONTENT_TYPES: dict[str, str] = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
}

# Reverse mapping for content-type → extension
EXT_FROM_CT: dict[str, str] = {
    "image/svg+xml": ".svg",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}


def _detect_ext(url: str, content_type: str) -> str:
    """Determine file extension from content-type header, falling back to URL."""
    ct = content_type.lower().split(";")[0].strip()
    ext = EXT_FROM_CT.get(ct)
    if ext:
        return ext
    # Fallback: extract from URL path
    path = url.split("?")[0].split("#")[0]
    ext_from_url = Path(path).suffix.lower()
    if ext_from_url in CONTENT_TYPES:
        return ext_from_url
    return ".bin"


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    bucket = os.environ.get("R2_BUCKET", "jobseek-assets")
    public_base = os.environ.get("R2_DOMAIN_URL", "").rstrip("/")

    if not dry_run:
        import boto3

        if not public_base:
            print("Error: R2_DOMAIN_URL environment variable is required")
            sys.exit(1)

        client = boto3.client(
            "s3",
            endpoint_url=os.environ["R2_ENDPOINT_URL"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        )

    csv_path = DATA_DIR / "companies.csv"
    rows: list[dict[str, str]] = []
    updated = 0

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    http = httpx.Client(follow_redirects=True, timeout=15)

    for row in rows:
        slug = row["slug"]
        for role, col in [("logo", "logo_url"), ("icon", "icon_url")]:
            url = row.get(col, "").strip()
            if not url:
                continue

            # Skip if already an R2 URL
            if public_base and url.startswith(public_base):
                continue

            print(f"  {slug}/{role}: {url}")

            try:
                resp = http.get(url)
                resp.raise_for_status()
            except Exception as e:
                print(f"    FAILED to download: {e}")
                continue

            ct = resp.headers.get("content-type", "")
            ext = _detect_ext(url, ct)
            content_type = CONTENT_TYPES.get(ext, ct.split(";")[0].strip())
            key = f"companies/{slug}/{role}{ext}"

            if dry_run:
                print(f"    Would upload {key} ({content_type}, {len(resp.content):,} bytes)")
            else:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=resp.content,
                    ContentType=content_type,
                    CacheControl="public, max-age=604800",
                )
                r2_url = f"{public_base}/{key}"
                row[col] = r2_url
                print(f"    Uploaded → {r2_url}")
                updated += 1

    http.close()

    if not dry_run and updated > 0:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nUpdated {updated} URL(s) in companies.csv")
    elif dry_run:
        print(f"\nDry run: would update {updated} URL(s)")
    else:
        print("\nNo URLs updated.")


if __name__ == "__main__":
    main()
