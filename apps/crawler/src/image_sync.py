"""Upload company images from data/images/ to Cloudflare R2 and update CSVs.

Run by CI on pull requests (upload-company-images workflow). Reads image files
committed by ``ws submit``, uploads them to R2, writes the public URLs into
``companies.csv``, and deletes the local image directories so the repo stays
clean.

Environment variables:
    R2_ENDPOINT_URL  — S3-compatible API endpoint
    R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY — write credentials
    R2_BUCKET        — bucket name (e.g. ``jobseek-assets``)
    R2_DOMAIN_URL    — public base URL (e.g. ``https://jobseek-assets.colophon-group.org``)
"""

from __future__ import annotations

import csv
import os
import shutil
import sys

import boto3

from src.shared.constants import DATA_DIR

IMAGES_DIR = DATA_DIR / "images"

CONTENT_TYPES: dict[str, str] = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
}


def _s3_client():
    """Create an S3-compatible client for R2."""
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )


def upload_images() -> dict[str, dict[str, str]]:
    """Upload images from data/images/<slug>/ to R2.

    Returns:
        Mapping of slug to {"logo_url": ..., "icon_url": ...} with R2 public URLs.
    """
    if not IMAGES_DIR.exists():
        return {}

    bucket = os.environ["R2_BUCKET"]
    public_base = os.environ["R2_DOMAIN_URL"].rstrip("/")
    client = _s3_client()
    results: dict[str, dict[str, str]] = {}

    for slug_dir in sorted(IMAGES_DIR.iterdir()):
        if not slug_dir.is_dir():
            continue
        slug = slug_dir.name
        urls: dict[str, str] = {}

        for role in ("logo", "icon"):
            files = list(slug_dir.glob(f"{role}.*"))
            if not files:
                continue
            img_file = files[0]
            ext = img_file.suffix.lower()
            content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
            key = f"companies/{slug}/{role}{ext}"

            client.upload_file(
                str(img_file),
                bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "CacheControl": "public, max-age=604800",
                },
            )
            urls[f"{role}_url"] = f"{public_base}/{key}"
            print(f"  Uploaded {key} ({content_type})")

        if urls:
            results[slug] = urls

    return results


def update_csv(url_map: dict[str, dict[str, str]]) -> None:
    """Update companies.csv with R2 URLs for uploaded images."""
    csv_path = DATA_DIR / "companies.csv"
    rows: list[dict[str, str]] = []

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row in reader:
            slug = row["slug"]
            if slug in url_map:
                row.update(url_map[slug])
            rows.append(row)

    with open(csv_path, "w", newline="\n") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def cleanup(slugs: list[str]) -> None:
    """Remove processed image directories."""
    for slug in slugs:
        slug_dir = IMAGES_DIR / slug
        if slug_dir.exists():
            shutil.rmtree(slug_dir)
            print(f"  Cleaned up {slug_dir.relative_to(DATA_DIR)}")

    # Remove images dir if empty
    if IMAGES_DIR.exists() and not any(IMAGES_DIR.iterdir()):
        IMAGES_DIR.rmdir()


def main() -> None:
    """Entry point for CI."""
    if not IMAGES_DIR.exists() or not any(IMAGES_DIR.iterdir()):
        print("No images to upload.")
        return

    print("Uploading images to R2...")
    url_map = upload_images()

    if not url_map:
        print("No images uploaded.")
        return

    print(f"\nUpdating companies.csv with {len(url_map)} URL(s)...")
    update_csv(url_map)

    print("\nCleaning up image directories...")
    cleanup(list(url_map.keys()))

    print("\nDone.")
    sys.exit(0)


if __name__ == "__main__":
    main()
