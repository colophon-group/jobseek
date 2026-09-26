"""Find low-resolution company icons that duplicate a better logo asset.

Run from the repository root with ``apps/crawler/.venv/bin/python``. The default
mode only writes a CSV report. ``--stage`` copies strict matches to
``apps/crawler/data/images/<slug>/icon.*`` for the existing image-upload CI workflow.
Borderline matches can be staged only with a reviewed manifest pinned to the
exact URLs and asset hashes used in the visual review.
Downloaded assets are cached outside the repository so an interrupted audit
can resume without repeatedly fetching thousands of images.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
from pathlib import Path
from urllib.parse import urlparse
from xml.etree.ElementTree import ParseError

import httpx
from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "apps/crawler/data/companies.csv"
IMAGES_DIR = ROOT / "apps/crawler/data/images"
ASSET_HOST = "jobseek-assets.colophon-group.org"
MAX_BYTES = 4_000_000
ICON_REVIEW_SIZE = 96
ICON_STAGE_SIZE = 72
MIN_SOURCE_SCALE = 2
MAX_STRICT_MAE = 8.0
FIELDS = (
    "slug", "status", "icon_size", "logo_size", "white_mae", "black_mae",
    "logo_url", "icon_url", "error",
)


def _cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / hashlib.sha256(url.encode()).hexdigest()


async def _fetch(client: httpx.AsyncClient, cache_dir: Path, url: str) -> bytes:
    if urlparse(url).hostname != ASSET_HOST:
        raise ValueError("asset is not hosted on the project R2 domain")
    path = _cache_path(cache_dir, url)
    if path.exists():
        return path.read_bytes()
    response = await client.get(url)
    response.raise_for_status()
    data = response.content
    if len(data) > MAX_BYTES:
        raise ValueError(f"asset exceeds {MAX_BYTES} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _image(data: bytes, url: str) -> tuple[Image.Image, bool]:
    svg = urlparse(url).path.lower().endswith(".svg") or data.lstrip().startswith(b"<svg")
    if svg:
        import cairosvg  # type: ignore[import-untyped]

        data = cairosvg.svg2png(bytestring=data, output_width=512)
    with Image.open(io.BytesIO(data)) as image:
        image.seek(0)
        image.load()
        return image.convert("RGBA"), svg


def _mae(left: Image.Image, right: Image.Image, background: str) -> float:
    canvas_a = Image.new("RGBA", left.size, background)
    canvas_b = Image.new("RGBA", right.size, background)
    canvas_a.alpha_composite(left)
    canvas_b.alpha_composite(right)
    diff = ImageChops.difference(canvas_a.convert("RGB"), canvas_b.convert("RGB"))
    return round(sum(ImageStat.Stat(diff).mean) / 3, 2)


def compare_images(logo: Image.Image, icon: Image.Image) -> tuple[float, float]:
    """Compare equal artwork at display size on both light and dark surfaces."""
    logo_ratio = logo.width / logo.height
    icon_ratio = icon.width / icon.height
    if abs(logo_ratio / icon_ratio - 1) > 0.03:
        return 255.0, 255.0
    sample_size = (96, 96)
    left = logo.resize(sample_size, Image.Resampling.LANCZOS)
    right = icon.resize(sample_size, Image.Resampling.LANCZOS)
    return _mae(left, right, "white"), _mae(left, right, "black")


def _record(row: dict[str, str], status: str, **values: object) -> dict[str, str]:
    result = {field: "" for field in FIELDS}
    result.update({field: row.get(field, "") for field in ("slug", "logo_url", "icon_url")})
    result["status"] = status
    result.update({key: str(value) for key, value in values.items()})
    return result


async def audit_one(
    row: dict[str, str], client: httpx.AsyncClient, cache_dir: Path,
) -> dict[str, str]:
    logo_url, icon_url = row.get("logo_url", ""), row.get("icon_url", "")
    if not logo_url or not icon_url or logo_url == icon_url:
        return _record(row, "skip_missing_or_same_url")
    try:
        icon_data = await _fetch(client, cache_dir, icon_url)
        icon, icon_svg = _image(icon_data, icon_url)
        icon_size = f"{icon.width}x{icon.height}" + (" svg" if icon_svg else "")
        if icon_svg or max(icon.size) >= ICON_REVIEW_SIZE:
            return _record(row, "skip_sufficient_resolution", icon_size=icon_size)
        logo_data = await _fetch(client, cache_dir, logo_url)
        logo, logo_svg = _image(logo_data, logo_url)
        logo_size = f"{logo.width}x{logo.height}" + (" svg" if logo_svg else "")
        if not logo_svg and max(logo.size) < MIN_SOURCE_SCALE * max(icon.size):
            return _record(
                row, "skip_no_better_source", icon_size=icon_size, logo_size=logo_size,
            )
        white, black = compare_images(logo, icon)
        strict = max(white, black) <= MAX_STRICT_MAE
        if strict and max(icon.size) < ICON_STAGE_SIZE:
            status = "stageable"
        elif max(white, black) <= 25:
            status = "review_similar_artwork"
        else:
            status = "different_artwork"
        return _record(
            row, status, icon_size=icon_size, logo_size=logo_size,
            white_mae=white, black_mae=black,
        )
    except (httpx.HTTPError, OSError, ValueError, ImportError, ParseError) as exc:
        return _record(row, "error", error=f"{type(exc).__name__}: {exc}")


def load_reviewed_manifest(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    with path.open(newline="") as file:
        reader = csv.DictReader(file)
        required = {"slug", "logo_url", "icon_url", "logo_sha256", "icon_sha256"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"reviewed manifest must contain {', '.join(sorted(required))}")
        reviewed = {row["slug"]: row for row in reader}
    return reviewed


def stage_matches(
    records: list[dict[str, str]], cache_dir: Path,
    reviewed: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Stage verified originals without overwriting any existing image work."""
    reviewed = reviewed or {}
    staged: list[str] = []
    for record in records:
        status = record["status"]
        if status not in {"stageable", "review_similar_artwork"}:
            continue
        slug = record["slug"]
        target_dir = IMAGES_DIR / slug
        if target_dir.exists():
            continue
        source_url = record["logo_url"]
        if status == "review_similar_artwork":
            approval = reviewed.get(slug)
            if not approval or any(
                approval[key] != record[key] for key in ("logo_url", "icon_url")
            ):
                continue
            if any(
                hashlib.sha256(_cache_path(cache_dir, record[f"{role}_url"]).read_bytes())
                .hexdigest() != approval[f"{role}_sha256"]
                for role in ("logo", "icon")
            ):
                continue
        source_path = urlparse(source_url).path
        if not source_path.startswith(f"/companies/{slug}/"):
            continue
        suffix = Path(source_path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".ico"}:
            continue
        source = _cache_path(cache_dir, source_url)
        target_dir.mkdir(parents=True)
        (target_dir / f"icon{suffix}").write_bytes(source.read_bytes())
        staged.append(slug)
    return staged


async def run(args: argparse.Namespace) -> None:
    with CSV_PATH.open(newline="") as file:
        rows = list(csv.DictReader(file))
    if args.limit:
        rows = rows[: args.limit]
    semaphore = asyncio.Semaphore(args.concurrency)
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(timeout=20, limits=limits, follow_redirects=False) as client:
        async def inspect(row: dict[str, str]) -> dict[str, str]:
            async with semaphore:
                return await audit_one(row, client, args.cache_dir)

        records = await asyncio.gather(*(inspect(row) for row in rows))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    counts: dict[str, int] = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    print(f"Audit: {counts}; report: {args.output}")
    if args.stage:
        reviewed = load_reviewed_manifest(args.reviewed_manifest)
        staged = stage_matches(records, args.cache_dir, reviewed)
        print(f"Staged {len(staged)} verified matches: {', '.join(staged)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/jobseek-icon-audit-cache"))
    parser.add_argument("--output", type=Path, default=Path("/tmp/jobseek-icon-audit.csv"))
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--stage", action="store_true", help="Stage verified matches for image CI")
    parser.add_argument("--reviewed-manifest", type=Path)
    args = parser.parse_args()
    if args.concurrency < 1 or args.concurrency > 40:
        parser.error("--concurrency must be between 1 and 40")
    if args.reviewed_manifest and not args.stage:
        parser.error("--reviewed-manifest requires --stage")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
