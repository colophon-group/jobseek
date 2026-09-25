"""Conservative bulk icon repair gates."""

from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "audit_company_icons.py"
SPEC = importlib.util.spec_from_file_location("audit_company_icons", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def _logo(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((size // 8, size // 8, size * 7 // 8, size * 7 // 8), fill="#145baa")
    return image


def _cache_image(cache_dir: Path, url: str, image: Image.Image) -> None:
    output = io.BytesIO()
    image.save(output, "PNG")
    path = audit._cache_path(cache_dir, url)
    path.write_bytes(output.getvalue())


async def test_same_artwork_low_resolution_is_stageable(tmp_path):
    logo = _logo(256)
    icon = logo.resize((32, 32), Image.Resampling.LANCZOS)
    row = {
        "slug": "example",
        "logo_url": "https://jobseek-assets.colophon-group.org/companies/example/logo.png",
        "icon_url": "https://jobseek-assets.colophon-group.org/companies/example/icon.png",
    }
    _cache_image(tmp_path, row["logo_url"], logo)
    _cache_image(tmp_path, row["icon_url"], icon)

    result = await audit.audit_one(row, None, tmp_path)

    assert result["status"] == "stageable"
    assert result["icon_size"] == "32x32"


async def test_distinct_mark_is_never_staged(tmp_path):
    logo = _logo(256)
    icon = Image.new("RGBA", (32, 32), "#e03030")
    row = {
        "slug": "example",
        "logo_url": "https://jobseek-assets.colophon-group.org/companies/example/logo.png",
        "icon_url": "https://jobseek-assets.colophon-group.org/companies/example/icon.png",
    }
    _cache_image(tmp_path, row["logo_url"], logo)
    _cache_image(tmp_path, row["icon_url"], icon)

    result = await audit.audit_one(row, None, tmp_path)

    assert result["status"] == "different_artwork"


async def test_adequate_resolution_is_not_staged(tmp_path):
    row = {
        "slug": "example",
        "logo_url": "https://jobseek-assets.colophon-group.org/companies/example/logo.png",
        "icon_url": "https://jobseek-assets.colophon-group.org/companies/example/icon.png",
    }
    _cache_image(tmp_path, row["icon_url"], _logo(128))

    result = await audit.audit_one(row, None, tmp_path)

    assert result["status"] == "skip_sufficient_resolution"


def test_staging_does_not_overwrite_existing_work(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "IMAGES_DIR", tmp_path / "images")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    url = "https://jobseek-assets.colophon-group.org/companies/example/logo.png"
    _cache_image(cache_dir, url, _logo(256))
    target = audit.IMAGES_DIR / "example"
    target.mkdir(parents=True)
    existing = target / "icon.png"
    existing.write_bytes(b"existing")

    staged = audit.stage_matches(
        [{"slug": "example", "status": "stageable", "logo_url": url}],
        cache_dir,
    )

    assert staged == []
    assert existing.read_bytes() == b"existing"


def test_reviewed_staging_requires_exact_asset_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "IMAGES_DIR", tmp_path / "images")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    logo_url = "https://jobseek-assets.colophon-group.org/companies/example/logo.png"
    icon_url = "https://jobseek-assets.colophon-group.org/companies/example/icon.png"
    _cache_image(cache_dir, logo_url, _logo(256))
    _cache_image(cache_dir, icon_url, _logo(32))
    record = {
        "slug": "example",
        "status": "review_similar_artwork",
        "logo_url": logo_url,
        "icon_url": icon_url,
    }
    approval = {
        "slug": "example",
        "logo_url": logo_url,
        "icon_url": icon_url,
        "logo_sha256": hashlib.sha256(
            audit._cache_path(cache_dir, logo_url).read_bytes()
        ).hexdigest(),
        "icon_sha256": "wrong",
    }

    assert audit.stage_matches([record], cache_dir, {"example": approval}) == []
    approval["icon_sha256"] = hashlib.sha256(
        audit._cache_path(cache_dir, icon_url).read_bytes()
    ).hexdigest()
    assert audit.stage_matches([record], cache_dir, {"example": approval}) == ["example"]
    assert (audit.IMAGES_DIR / "example" / "icon.png").read_bytes() == (
        audit._cache_path(cache_dir, logo_url).read_bytes()
    )
