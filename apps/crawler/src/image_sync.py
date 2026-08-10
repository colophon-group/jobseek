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

import argparse
import csv
import hashlib
import os
import shutil
import sys
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path

import boto3
from PIL import Image, UnidentifiedImageError

from src.shared.constants import DATA_DIR, SLUG_RE

IMAGES_DIR = DATA_DIR / "images"

# Cap icon dimensions at 128×128 (preserve aspect ratio). Web renders top out
# at 36 CSS px (#2867); 128 covers retina 4× DPR with headroom and keeps the
# WebP file size around a few KB even for complex logos. See #2869.
ICON_MAX_DIM = 128

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


def process_icon(path: str) -> bytes:
    """Convert an icon image to WebP bytes, capped at ICON_MAX_DIM per side."""
    with Image.open(path) as image:
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        if max(image.size) > ICON_MAX_DIM:
            # thumbnail() preserves aspect ratio; LANCZOS keeps glyph edges sharp
            # at small sizes vs the default BICUBIC. No-op for already-small icons.
            image.thumbnail((ICON_MAX_DIM, ICON_MAX_DIM), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="WEBP", quality=82, method=6)
        return buffer.getvalue()


def _content_addressed_key(slug: str, role: str, extension: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    return f"companies/{slug}/{role}-{digest}{extension}"


def upload_icon(client, bucket: str, slug: str, img_file: Path) -> tuple[str, str]:
    """Upload an icon, preferring WebP but falling back to the source asset."""
    try:
        body = process_icon(str(img_file))
        extension = ".webp"
        content_type = "image/webp"
    except (OSError, UnidentifiedImageError):
        body = img_file.read_bytes()
        extension = img_file.suffix.lower()
        content_type = CONTENT_TYPES.get(extension, "application/octet-stream")

    key = _content_addressed_key(slug, "icon", extension, body)
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
        CacheControl="public, max-age=31536000, immutable",
    )
    return key, content_type


def upload_images(slugs: Iterable[str] | None = None) -> dict[str, dict[str, str]]:
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

    slug_dirs = (
        [IMAGES_DIR / slug for slug in dict.fromkeys(slugs)]
        if slugs is not None
        else sorted(IMAGES_DIR.iterdir())
    )
    for slug_dir in slug_dirs:
        if not slug_dir.is_dir():
            continue
        slug = slug_dir.name
        urls: dict[str, str] = {}

        for role in ("logo", "icon"):
            files = sorted(slug_dir.glob(f"{role}.*"))
            if not files:
                continue
            img_file = files[0]
            if role == "icon":
                key, content_type = upload_icon(client, bucket, slug, img_file)
            else:
                ext = img_file.suffix.lower()
                content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
                body = img_file.read_bytes()
                key = _content_addressed_key(slug, role, ext, body)
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    CacheControl="public, max-age=31536000, immutable",
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slug",
        action="append",
        required=True,
        help="Company image directory to process; repeat for multiple companies",
    )
    args = parser.parse_args()
    slugs = list(dict.fromkeys(args.slug))
    invalid = [slug for slug in slugs if not SLUG_RE.fullmatch(slug)]
    if invalid:
        parser.error(f"invalid company slug(s): {', '.join(invalid)}")

    if not IMAGES_DIR.exists() or not any((IMAGES_DIR / slug).is_dir() for slug in slugs):
        print("No images to upload.")
        return

    print("Uploading images to R2...")
    url_map = upload_images(slugs)

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
