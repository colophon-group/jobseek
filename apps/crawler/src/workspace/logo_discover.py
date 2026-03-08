"""Auto-discover logo/icon candidates from a company homepage."""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import httpx

# Browser-like headers for logo fetching — many sites block default httpx UA
_LOGO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class LogoCandidate:
    """A discovered logo or icon candidate."""

    url: str  # Absolute URL ("" for embedded SVG)
    sources: list[str] = field(default_factory=list)
    role: str = "logo"  # "logo" | "icon"
    score: float = 0.0
    width: int | None = None
    height: int | None = None
    is_svg: bool = False
    embedded_svg: str | None = None  # Raw SVG markup for inline SVGs
    artifact_path: str | None = None  # Set after download
    original_artifact_path: str | None = None
    png_artifact_path: str | None = None
    filename: str | None = None
    content_type: str | None = None
    file_size_bytes: int | None = None
    aspect_ratio: float | None = None
    is_square: bool | None = None
    has_transparency: bool | None = None
    ocr_text: str | None = None


# ── HTMLParser-based extractor ─────────────────────────────────────────


class _LogoExtractor(HTMLParser):
    """Single-pass HTML parser that extracts logo/icon candidates."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        # Normalize base for homepage link detection
        self._base_normalized = re.sub(r"/$", "", base_url.split("?")[0].split("#")[0])

        self.candidates: list[LogoCandidate] = []

        # Context tracking
        self._in_head = False
        self._in_header = False
        self._in_nav = False
        self._in_home_link = False
        self._home_link_depth = 0

        # SVG accumulation
        self._in_svg = False
        self._svg_depth = 0
        self._svg_parts: list[str] = []
        self._svg_context: str | None = None  # "header" | "nav" | "home_link"

        # JSON-LD accumulation
        self._in_jsonld = False
        self._jsonld_parts: list[str] = []

    def _resolve(self, url: str) -> str | None:
        """Resolve URL, filtering data: URIs."""
        if not url or url.startswith("data:"):
            return None
        return urljoin(self.base_url, url)

    def _is_home_href(self, href: str | None) -> bool:
        """Check if href points to the homepage."""
        if not href:
            return False
        if href in ("/", "#", ""):
            return True
        resolved = urljoin(self.base_url, href)
        normalized = re.sub(r"/$", "", resolved.split("?")[0].split("#")[0])
        return normalized == self._base_normalized

    def _has_logo_hint(self, attrs: dict[str, str]) -> bool:
        """Check if attributes suggest this is a logo element."""
        for attr in ("class", "id", "alt", "data-testid", "aria-label"):
            val = attrs.get(attr, "")
            if val and "logo" in val.lower():
                return True
        return False

    def _add(self, url: str | None, source: str, role: str, score: float, **kw: object) -> None:
        """Add a candidate, deduplicating by URL."""
        if url is None and not kw.get("embedded_svg"):
            return
        self.candidates.append(
            LogoCandidate(
                url=url or "",
                sources=[source],
                role=role,
                score=score,
                is_svg=bool(kw.get("is_svg")),
                embedded_svg=kw.get("embedded_svg"),  # type: ignore[arg-type]
            )
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        tag_l = tag.lower()

        # Context entry
        if tag_l == "head":
            self._in_head = True
        elif tag_l == "header":
            self._in_header = True
        elif tag_l == "nav":
            self._in_nav = True
        elif tag_l == "a":
            if self._is_home_href(a.get("href")):
                self._in_home_link = True
                self._home_link_depth = 1
            elif self._in_home_link:
                self._home_link_depth += 1

        # SVG start
        if tag_l == "svg":
            if self._in_svg:
                self._svg_depth += 1
                self._svg_parts.append(self._rebuild_tag(tag, attrs))
            elif self._in_header or self._in_nav or self._in_home_link:
                self._in_svg = True
                self._svg_depth = 1
                self._svg_parts = [self._rebuild_tag(tag, attrs)]
                if self._in_header:
                    self._svg_context = "header"
                elif self._in_nav:
                    self._svg_context = "nav"
                else:
                    self._svg_context = "home_link"
            return

        if self._in_svg:
            self._svg_parts.append(self._rebuild_tag(tag, attrs))
            self._svg_depth += 1
            return

        # JSON-LD script
        if tag_l == "script":
            st = a.get("type", "").lower()
            if st == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_parts = []
                return

        # <link> tags (head only)
        if tag_l == "link" and self._in_head:
            rel = a.get("rel", "").lower()
            href = a.get("href")
            resolved = self._resolve(href) if href else None
            if "apple-touch-icon" in rel and resolved:
                self._add(resolved, "apple-touch-icon", "icon", 0.85)
            elif rel == "icon" and resolved:
                self._add(resolved, "icon", "icon", 0.60)

        # <meta> OG/Twitter images (head only)
        if tag_l == "meta" and self._in_head:
            prop = a.get("property", "").lower()
            name = a.get("name", "").lower()
            content = a.get("content", "")
            if prop == "og:image" and content:
                resolved = self._resolve(content)
                if resolved:
                    self._add(resolved, "og:image", "logo", 0.75)
            elif name == "twitter:image" and content:
                resolved = self._resolve(content)
                if resolved:
                    self._add(resolved, "twitter:image", "logo", 0.70)

        # <img> tags
        if tag_l == "img":
            src = a.get("src")
            resolved = self._resolve(src) if src else None
            if not resolved:
                return

            # img with logo hint (anywhere in page)
            if self._has_logo_hint(a):
                self._add(resolved, "logo_class_img", "logo", 0.85)
            # img in header
            elif self._in_header:
                self._add(resolved, "header_img", "logo", 0.80)
            # img in nav
            elif self._in_nav:
                self._add(resolved, "nav_img", "logo", 0.75)

            # img inside home link
            if self._in_home_link:
                self._add(resolved, "home_link_img", "logo", 0.75)

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()

        if self._in_svg:
            self._svg_parts.append(f"</{tag}>")
            self._svg_depth -= 1
            if self._svg_depth <= 0:
                svg_markup = "".join(self._svg_parts)
                source = f"{self._svg_context}_svg"
                score = 0.85 if self._svg_context == "header" else 0.80
                if self._svg_context == "home_link":
                    source = "home_link_svg"
                    score = 0.75
                self._add(
                    None,
                    source,
                    "logo",
                    score,
                    is_svg=True,
                    embedded_svg=svg_markup,
                )
                self._in_svg = False
                self._svg_context = None
            return

        # Context exit
        if tag_l == "head":
            self._in_head = False
        elif tag_l == "header":
            self._in_header = False
        elif tag_l == "nav":
            self._in_nav = False
        elif tag_l == "a" and self._in_home_link:
            self._home_link_depth -= 1
            if self._home_link_depth <= 0:
                self._in_home_link = False

        # JSON-LD end
        if tag_l == "script" and self._in_jsonld:
            self._in_jsonld = False
            raw = "".join(self._jsonld_parts)
            self._parse_jsonld(raw)

    def handle_data(self, data: str) -> None:
        if self._in_svg:
            self._svg_parts.append(data)
        elif self._in_jsonld:
            self._jsonld_parts.append(data)

    def _parse_jsonld(self, raw: str) -> None:
        """Extract Organization.logo from JSON-LD."""
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return
        self._extract_org_logo(obj)

    def _extract_org_logo(self, obj: object) -> None:
        """Recursively find Organization logo in JSON-LD."""
        if isinstance(obj, list):
            for item in obj:
                self._extract_org_logo(item)
            return
        if not isinstance(obj, dict):
            return

        obj_type = obj.get("@type", "")
        if isinstance(obj_type, list):
            types = [t.lower() for t in obj_type if isinstance(t, str)]
        elif isinstance(obj_type, str):
            types = [obj_type.lower()]
        else:
            types = []

        if "organization" in types or "corporation" in types:
            logo = obj.get("logo")
            if isinstance(logo, str) and logo:
                resolved = self._resolve(logo)
                if resolved:
                    self._add(resolved, "json-ld:Organization", "logo", 0.90)
            elif isinstance(logo, dict):
                logo_url = logo.get("url") or logo.get("contentUrl")
                if isinstance(logo_url, str) and logo_url:
                    resolved = self._resolve(logo_url)
                    if resolved:
                        self._add(resolved, "json-ld:Organization", "logo", 0.90)

        # Check @graph
        if "@graph" in obj:
            self._extract_org_logo(obj["@graph"])
        # Check nested publisher/organization
        for key in ("publisher", "organization", "author"):
            if key in obj:
                self._extract_org_logo(obj[key])

    @staticmethod
    def _rebuild_tag(tag: str, attrs: list[tuple[str, str | None]]) -> str:
        """Rebuild an opening tag from parsed attributes."""
        parts = [f"<{tag}"]
        for k, v in attrs:
            if v is None:
                parts.append(f" {k}")
            else:
                parts.append(f' {k}="{v}"')
        parts.append(">")
        return "".join(parts)


# ── Public API ─────────────────────────────────────────────────────────


def discover_logos(html: str, base_url: str) -> list[LogoCandidate]:
    """Parse HTML and return ranked, deduplicated logo/icon candidates.

    Always appends a Google Favicon API fallback at score 0.40.
    """
    parser = _LogoExtractor(base_url)
    parser.feed(html)

    candidates = parser.candidates

    # Dedup by URL (merge sources, boost score)
    deduped = _dedup_candidates(candidates)

    # Sort by score descending
    deduped.sort(key=lambda c: c.score, reverse=True)

    # Append API-based fallbacks (these don't require scraping the homepage)
    from urllib.parse import urlparse

    domain = urlparse(base_url).netloc
    if domain:
        existing_urls = {c.url for c in deduped}

        # DuckDuckGo icon API — higher-resolution than Google favicon
        ddg_url = f"https://icons.duckduckgo.com/ip3/{domain}.ico"
        if ddg_url not in existing_urls:
            deduped.append(
                LogoCandidate(
                    url=ddg_url,
                    sources=["duckduckgo-icon-api"],
                    role="icon",
                    score=0.45,
                )
            )

        # Google Favicon API — reliable last resort
        favicon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
        if favicon_url not in existing_urls:
            deduped.append(
                LogoCandidate(
                    url=favicon_url,
                    sources=["google-favicon-api"],
                    role="icon",
                    score=0.40,
                )
            )

    return deduped


def _dedup_candidates(candidates: list[LogoCandidate]) -> list[LogoCandidate]:
    """Merge candidates with the same URL. Boost score +0.1 per extra source."""
    by_key: dict[str, LogoCandidate] = {}

    for c in candidates:
        # For embedded SVGs, use the SVG content as key
        key = c.embedded_svg if c.embedded_svg else c.url
        if not key:
            continue

        if key in by_key:
            existing = by_key[key]
            # Merge sources (avoid duplicates)
            for src in c.sources:
                if src not in existing.sources:
                    existing.sources.append(src)
                    existing.score = min(1.0, existing.score + 0.1)
            # Keep the higher role priority ("logo" > "icon")
            if c.role == "logo":
                existing.role = "logo"
        else:
            by_key[key] = LogoCandidate(
                url=c.url,
                sources=list(c.sources),
                role=c.role,
                score=c.score,
                width=c.width,
                height=c.height,
                is_svg=c.is_svg,
                embedded_svg=c.embedded_svg,
            )

    return list(by_key.values())


def download_candidates(
    candidates: list[LogoCandidate],
    artifact_dir: Path,
    timeout: float = 5.0,
) -> list[LogoCandidate]:
    """Download candidates and save as artifacts.

    Each candidate is saved in original format + PNG copy for model inspection.
    Returns the list of successfully downloaded candidates with artifact_path set.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    successful: list[LogoCandidate] = []

    for i, candidate in enumerate(candidates, 1):
        try:
            if candidate.embedded_svg:
                # Save raw SVG
                svg_path = artifact_dir / f"candidate-{i}.svg"
                svg_markup = candidate.embedded_svg
                svg_bytes = svg_markup.encode("utf-8")
                svg_path.write_bytes(svg_bytes)
                candidate.artifact_path = str(svg_path)
                candidate.original_artifact_path = str(svg_path)
                candidate.filename = svg_path.name
                candidate.content_type = "image/svg+xml"
                candidate.file_size_bytes = len(svg_bytes)
                _set_svg_dimensions(candidate, svg_markup)
                # Try to rasterize to PNG
                png_path = artifact_dir / f"candidate-{i}.png"
                saved_png = _try_svg_to_png(svg_markup, png_path)
                if saved_png:
                    candidate.png_artifact_path = str(saved_png)
                    _populate_tech_from_image_path(candidate, saved_png)
                successful.append(candidate)
            elif candidate.url:
                data, content_type = _fetch_image(candidate.url, timeout)
                if data is None:
                    continue
                # Save original format
                ext = _ext_from_content_type(content_type)
                orig_path = artifact_dir / f"candidate-{i}{ext}"
                orig_path.write_bytes(data)
                candidate.artifact_path = str(orig_path)
                candidate.original_artifact_path = str(orig_path)
                candidate.filename = orig_path.name
                candidate.content_type = content_type
                candidate.file_size_bytes = len(data)

                # Save PNG copy for all non-PNG formats.
                png_path = artifact_dir / f"candidate-{i}.png"
                if ext == ".png":
                    candidate.png_artifact_path = str(orig_path)
                    _populate_tech_from_image_bytes(candidate, data)
                elif ext == ".svg":
                    saved = _try_svg_bytes_to_png(data, png_path)
                    if saved:
                        candidate.png_artifact_path = str(saved)
                        _populate_tech_from_image_path(candidate, saved)
                    else:
                        _set_svg_dimensions(candidate, data.decode("utf-8", errors="ignore"))
                else:
                    saved = _save_as_png(data, png_path)
                    if saved:
                        candidate.png_artifact_path = str(saved)
                        _populate_tech_from_image_path(candidate, saved)
                    else:
                        _populate_tech_from_image_bytes(candidate, data)

                if candidate.aspect_ratio is None and candidate.width and candidate.height:
                    candidate.aspect_ratio = round(candidate.width / candidate.height, 3)
                if candidate.is_square is None and candidate.width and candidate.height:
                    candidate.is_square = candidate.width == candidate.height
                successful.append(candidate)
        except Exception:
            continue

    # Save candidates.json for later reference by --logo-candidate/--icon-candidate
    _save_candidates_json(successful, artifact_dir)

    return successful


def _fetch_image(url: str, timeout: float) -> tuple[bytes | None, str]:
    """Fetch image bytes and content-type."""
    try:
        resp = httpx.get(url, headers=_LOGO_HEADERS, follow_redirects=True, timeout=timeout)
        ct = resp.headers.get("content-type", "")
        if resp.status_code >= 400:
            return None, ct
        return resp.content, ct
    except Exception:
        return None, ""


def _save_as_png(data: bytes, png_path: Path) -> Path | None:
    """Convert image data to PNG. Returns path on success, None on failure."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGBA", "RGB"):
            img = img.convert("RGBA")
        img.save(png_path, "PNG")
        return png_path
    except Exception:
        return None


def _try_svg_bytes_to_png(svg_bytes: bytes, png_path: Path) -> Path | None:
    """Try to rasterize SVG bytes to PNG."""
    return _try_svg_to_png(svg_bytes.decode("utf-8", errors="ignore"), png_path)


def _try_svg_to_png(svg_markup: str, png_path: Path) -> Path | None:
    """Try to rasterize SVG to PNG. Returns path on success, None on failure."""
    try:
        import cairosvg  # type: ignore[import-untyped]

        cairosvg.svg2png(bytestring=svg_markup.encode(), write_to=str(png_path))
        return png_path
    except Exception:
        return None


def _set_svg_dimensions(candidate: LogoCandidate, svg_markup: str) -> None:
    """Populate size metadata for SVG markup using width/height or viewBox."""
    width = _parse_svg_dimension(svg_markup, "width")
    height = _parse_svg_dimension(svg_markup, "height")

    if width is None or height is None:
        viewbox = re.search(
            r'viewBox=["\']\s*[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+\s+([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s*["\']',
            svg_markup,
            re.IGNORECASE,
        )
        if viewbox:
            if width is None:
                width = _to_int(viewbox.group(1))
            if height is None:
                height = _to_int(viewbox.group(2))

    if width and height:
        candidate.width = width
        candidate.height = height
        candidate.aspect_ratio = round(width / height, 3)
        candidate.is_square = width == height


def _parse_svg_dimension(svg_markup: str, name: str) -> int | None:
    """Extract numeric width/height from SVG attributes."""
    m = re.search(rf'{name}=["\']\s*([-+]?\d*\.?\d+)(?:px)?\s*["\']', svg_markup, re.IGNORECASE)
    if not m:
        return None
    return _to_int(m.group(1))


def _to_int(value: str) -> int | None:
    """Convert a numeric string to a rounded positive int."""
    try:
        parsed = int(round(float(value)))
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _populate_tech_from_image_path(candidate: LogoCandidate, image_path: Path) -> None:
    """Populate dimensions, transparency, and OCR from an image file."""
    try:
        with _open_image(image_path.read_bytes()) as img:
            _populate_tech_from_image(candidate, img)
    except Exception:
        return


def _populate_tech_from_image_bytes(candidate: LogoCandidate, image_bytes: bytes) -> None:
    """Populate dimensions, transparency, and OCR from image bytes."""
    try:
        with _open_image(image_bytes) as img:
            _populate_tech_from_image(candidate, img)
    except Exception:
        return


def _open_image(image_bytes: bytes):
    """Open image bytes as a Pillow image context."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    img.load()
    return img


def _populate_tech_from_image(candidate: LogoCandidate, img) -> None:
    """Populate candidate technical fields from a Pillow image."""
    rgba = img.convert("RGBA") if img.mode != "RGBA" else img

    candidate.width = rgba.width
    candidate.height = rgba.height
    if rgba.height:
        candidate.aspect_ratio = round(rgba.width / rgba.height, 3)
    candidate.is_square = rgba.width == rgba.height
    alpha = rgba.getchannel("A")
    min_alpha, _ = alpha.getextrema()
    candidate.has_transparency = min_alpha < 255
    candidate.ocr_text = _extract_ocr_text(rgba)


def _extract_ocr_text(img) -> str | None:
    """Best-effort OCR for candidate diagnostics."""
    try:
        import pytesseract  # type: ignore[import-untyped]
    except Exception:
        return None

    try:
        raw = pytesseract.image_to_string(img.convert("L"), config="--psm 6")
    except Exception:
        return None

    cleaned = re.sub(r"\s+", " ", raw).strip()
    if not cleaned:
        return None
    return cleaned[:80]


def _ext_from_content_type(ct: str) -> str:
    """Map content-type to file extension."""
    ct = ct.lower().split(";")[0].strip()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/webp": ".webp",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
    }
    return mapping.get(ct, ".png")


def _save_candidates_json(candidates: list[LogoCandidate], artifact_dir: Path) -> None:
    """Persist candidate metadata for later reference."""
    data = []
    for i, c in enumerate(candidates, 1):
        entry: dict[str, object] = {
            "index": i,
            "url": c.url,
            "sources": c.sources,
            "role": c.role,
            "score": c.score,
            "artifact_path": c.artifact_path,
            "original_artifact_path": c.original_artifact_path,
            "png_artifact_path": c.png_artifact_path,
            "filename": c.filename,
            "content_type": c.content_type,
            "file_size_bytes": c.file_size_bytes,
            "width": c.width,
            "height": c.height,
            "aspect_ratio": c.aspect_ratio,
            "is_square": c.is_square,
            "has_transparency": c.has_transparency,
            "ocr_text": c.ocr_text,
        }
        if c.embedded_svg:
            entry["embedded"] = True
        data.append(entry)
    (artifact_dir / "candidates.json").write_text(json.dumps(data, indent=2))
