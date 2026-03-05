"""Tests for logo_discover module — HTML parsing and candidate extraction."""

from __future__ import annotations

from src.workspace.logo_discover import LogoCandidate, _dedup_candidates, discover_logos

BASE = "https://example.com"


def _discover(html: str) -> list[LogoCandidate]:
    return discover_logos(html, BASE)


# ── Individual heuristic tests ──────────────────────────────────────


class TestJsonLd:
    def test_organization_logo_string(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "Organization", "logo": "https://example.com/logo.png"}
        </script>
        </head><body></body></html>
        """
        cs = _discover(html)
        logos = [c for c in cs if "json-ld:Organization" in c.sources]
        assert len(logos) == 1
        assert logos[0].url == "https://example.com/logo.png"
        assert logos[0].score >= 0.90

    def test_organization_logo_object(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "Organization", "logo": {"@type": "ImageObject", "url": "https://example.com/logo.svg"}}
        </script>
        </head><body></body></html>
        """
        cs = _discover(html)
        logos = [c for c in cs if "json-ld:Organization" in c.sources]
        assert len(logos) == 1
        assert logos[0].url == "https://example.com/logo.svg"

    def test_graph_organization(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@graph": [{"@type": "Organization", "logo": "/brand/logo.png"}]}
        </script>
        </head><body></body></html>
        """
        cs = _discover(html)
        logos = [c for c in cs if "json-ld:Organization" in c.sources]
        assert len(logos) == 1
        assert logos[0].url == "https://example.com/brand/logo.png"

    def test_corporation_type(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "Corporation", "logo": "https://example.com/corp-logo.png"}
        </script>
        </head><body></body></html>
        """
        cs = _discover(html)
        logos = [c for c in cs if "json-ld:Organization" in c.sources]
        assert len(logos) == 1


class TestAppleTouchIcon:
    def test_basic(self):
        html = """
        <html><head>
        <link rel="apple-touch-icon" href="/apple-touch-icon.png">
        </head><body></body></html>
        """
        cs = _discover(html)
        icons = [c for c in cs if "apple-touch-icon" in c.sources]
        assert len(icons) == 1
        assert icons[0].url == "https://example.com/apple-touch-icon.png"
        assert icons[0].role == "icon"
        assert icons[0].score >= 0.85

    def test_apple_touch_icon_precomposed(self):
        html = """
        <html><head>
        <link rel="apple-touch-icon-precomposed" href="/icon-pre.png">
        </head><body></body></html>
        """
        cs = _discover(html)
        icons = [c for c in cs if "apple-touch-icon" in c.sources]
        assert len(icons) == 1


class TestLogoClassImg:
    def test_class_contains_logo(self):
        html = """
        <html><head></head><body>
        <img class="site-logo" src="/img/logo.png">
        </body></html>
        """
        cs = _discover(html)
        logos = [c for c in cs if "logo_class_img" in c.sources]
        assert len(logos) == 1
        assert logos[0].url == "https://example.com/img/logo.png"
        assert logos[0].role == "logo"

    def test_id_contains_logo(self):
        html = """
        <html><head></head><body>
        <img id="company-logo" src="/logo.svg">
        </body></html>
        """
        cs = _discover(html)
        logos = [c for c in cs if "logo_class_img" in c.sources]
        assert len(logos) == 1

    def test_alt_contains_logo(self):
        html = """
        <html><head></head><body>
        <img alt="Acme Logo" src="/acme-logo.png">
        </body></html>
        """
        cs = _discover(html)
        logos = [c for c in cs if "logo_class_img" in c.sources]
        assert len(logos) == 1

    def test_data_testid_contains_logo(self):
        html = """
        <html><head></head><body>
        <img data-testid="header-logo" src="/header-logo.png">
        </body></html>
        """
        cs = _discover(html)
        logos = [c for c in cs if "logo_class_img" in c.sources]
        assert len(logos) == 1


class TestHeaderNav:
    def test_header_svg(self):
        html = """
        <html><head></head><body>
        <header>
        <svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="40"/></svg>
        </header>
        </body></html>
        """
        cs = _discover(html)
        svgs = [c for c in cs if "header_svg" in c.sources]
        assert len(svgs) == 1
        assert svgs[0].is_svg
        assert svgs[0].embedded_svg is not None
        assert "<circle" in svgs[0].embedded_svg
        assert svgs[0].score >= 0.85

    def test_nav_svg(self):
        html = """
        <html><head></head><body>
        <nav>
        <svg viewBox="0 0 50 50"><rect width="50" height="50"/></svg>
        </nav>
        </body></html>
        """
        cs = _discover(html)
        svgs = [c for c in cs if "nav_svg" in c.sources]
        assert len(svgs) == 1
        assert svgs[0].score >= 0.80

    def test_header_img(self):
        html = """
        <html><head></head><body>
        <header><img src="/header-img.png"></header>
        </body></html>
        """
        cs = _discover(html)
        imgs = [c for c in cs if "header_img" in c.sources]
        assert len(imgs) == 1
        assert imgs[0].url == "https://example.com/header-img.png"
        assert imgs[0].score >= 0.80

    def test_nav_img(self):
        html = """
        <html><head></head><body>
        <nav><img src="/nav-logo.png"></nav>
        </body></html>
        """
        cs = _discover(html)
        imgs = [c for c in cs if "nav_img" in c.sources]
        assert len(imgs) == 1
        assert imgs[0].score >= 0.75


class TestOgTwitter:
    def test_og_image(self):
        html = """
        <html><head>
        <meta property="og:image" content="https://cdn.example.com/og.jpg">
        </head><body></body></html>
        """
        cs = _discover(html)
        ogs = [c for c in cs if "og:image" in c.sources]
        assert len(ogs) == 1
        assert ogs[0].url == "https://cdn.example.com/og.jpg"
        assert ogs[0].role == "logo"

    def test_twitter_image(self):
        html = """
        <html><head>
        <meta name="twitter:image" content="/twitter-card.png">
        </head><body></body></html>
        """
        cs = _discover(html)
        twits = [c for c in cs if "twitter:image" in c.sources]
        assert len(twits) == 1
        assert twits[0].url == "https://example.com/twitter-card.png"


class TestHomeLinkImg:
    def test_img_in_home_link(self):
        html = """
        <html><head></head><body>
        <a href="/"><img src="/home-logo.png"></a>
        </body></html>
        """
        cs = _discover(html)
        home = [c for c in cs if "home_link_img" in c.sources]
        assert len(home) >= 1
        assert any(c.url == "https://example.com/home-logo.png" for c in home)

    def test_svg_in_home_link(self):
        html = """
        <html><head></head><body>
        <a href="https://example.com">
        <svg viewBox="0 0 24 24"><path d="M0 0h24v24H0z"/></svg>
        </a>
        </body></html>
        """
        cs = _discover(html)
        home = [c for c in cs if "home_link_svg" in c.sources]
        assert len(home) == 1
        assert home[0].is_svg

    def test_hash_link_is_home(self):
        html = """
        <html><head></head><body>
        <a href="#"><img src="/hash-logo.png"></a>
        </body></html>
        """
        cs = _discover(html)
        home = [c for c in cs if "home_link_img" in c.sources]
        assert len(home) >= 1


class TestFaviconLink:
    def test_icon_rel(self):
        html = """
        <html><head>
        <link rel="icon" href="/favicon.ico">
        </head><body></body></html>
        """
        cs = _discover(html)
        icons = [c for c in cs if "icon" in c.sources]
        assert len(icons) == 1
        assert icons[0].url == "https://example.com/favicon.ico"
        assert icons[0].role == "icon"
        assert icons[0].score >= 0.60


# ── Dedup tests ─────────────────────────────────────────────────────


class TestDedup:
    def test_same_url_merged(self):
        html = """
        <html><head>
        <meta property="og:image" content="https://example.com/logo.png">
        </head><body>
        <header><img src="https://example.com/logo.png"></header>
        </body></html>
        """
        cs = _discover(html)
        matching = [c for c in cs if c.url == "https://example.com/logo.png"]
        assert len(matching) == 1
        assert len(matching[0].sources) >= 2
        # Boosted score: original + 0.1 per extra source
        assert matching[0].score > 0.75

    def test_dedup_preserves_higher_role(self):
        """When merging, 'logo' role takes precedence over 'icon'."""
        candidates = [
            LogoCandidate(url="https://x.com/img.png", sources=["icon"], role="icon", score=0.60),
            LogoCandidate(
                url="https://x.com/img.png", sources=["og:image"], role="logo", score=0.75
            ),
        ]
        result = _dedup_candidates(candidates)
        assert len(result) == 1
        assert result[0].role == "logo"


# ── URL resolution ──────────────────────────────────────────────────


class TestUrlResolution:
    def test_relative_url_resolved(self):
        html = """
        <html><head>
        <link rel="apple-touch-icon" href="/icons/apple-touch.png">
        </head><body></body></html>
        """
        cs = _discover(html)
        icons = [c for c in cs if "apple-touch-icon" in c.sources]
        assert icons[0].url == "https://example.com/icons/apple-touch.png"

    def test_absolute_url_preserved(self):
        html = """
        <html><head>
        <meta property="og:image" content="https://cdn.other.com/img.jpg">
        </head><body></body></html>
        """
        cs = _discover(html)
        ogs = [c for c in cs if "og:image" in c.sources]
        assert ogs[0].url == "https://cdn.other.com/img.jpg"


# ── Filtering ───────────────────────────────────────────────────────


class TestFiltering:
    def test_data_uri_filtered(self):
        html = """
        <html><head></head><body>
        <header><img src="data:image/png;base64,iVBOR..."></header>
        </body></html>
        """
        cs = _discover(html)
        # Should only have google-favicon fallback
        non_fallback = [c for c in cs if "google-favicon-api" not in c.sources]
        assert len(non_fallback) == 0


# ── Empty/fallback ──────────────────────────────────────────────────


class TestFallback:
    def test_empty_html_only_google_fallback(self):
        cs = _discover("<html><head></head><body></body></html>")
        assert len(cs) == 1
        assert cs[0].sources == ["google-favicon-api"]
        assert cs[0].role == "icon"
        assert cs[0].score == 0.40
        assert "s2/favicons" in cs[0].url

    def test_google_fallback_always_present(self):
        html = """
        <html><head>
        <link rel="apple-touch-icon" href="/apple.png">
        </head><body></body></html>
        """
        cs = _discover(html)
        fallbacks = [c for c in cs if "google-favicon-api" in c.sources]
        assert len(fallbacks) == 1


# ── Sorting ─────────────────────────────────────────────────────────


class TestSorting:
    def test_sorted_by_score_descending(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "Organization", "logo": "/logo.svg"}
        </script>
        <link rel="icon" href="/favicon.ico">
        <meta property="og:image" content="/og.jpg">
        </head><body></body></html>
        """
        cs = _discover(html)
        scores = [c.score for c in cs]
        assert scores == sorted(scores, reverse=True)


# ── Embedded SVG ────────────────────────────────────────────────────


class TestEmbeddedSvg:
    def test_embedded_svg_has_correct_fields(self):
        html = """
        <html><head></head><body>
        <header>
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r="40" fill="blue"/>
        </svg>
        </header>
        </body></html>
        """
        cs = _discover(html)
        svgs = [c for c in cs if c.is_svg]
        assert len(svgs) >= 1
        svg = svgs[0]
        assert svg.url == ""
        assert svg.embedded_svg is not None
        assert "circle" in svg.embedded_svg
        assert svg.role == "logo"

    def test_nested_svg_elements(self):
        html = """
        <html><head></head><body>
        <nav>
        <svg viewBox="0 0 24 24">
          <g>
            <path d="M0 0"/>
            <path d="M1 1"/>
          </g>
        </svg>
        </nav>
        </body></html>
        """
        cs = _discover(html)
        svgs = [c for c in cs if c.is_svg]
        assert len(svgs) >= 1
        assert "<g>" in svgs[0].embedded_svg
        assert "<path" in svgs[0].embedded_svg
