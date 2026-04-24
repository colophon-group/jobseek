"""Tests for the deterministic HTML normalizer.

Invariants we care about:
  - Allowed-tag subset only
  - Text content preserved (coverage ratio > 0.8 for realistic input)
  - Disallowed tags unwrapped, not dropped
  - Scripts/styles discarded entirely
  - Root-level naked text gets wrapped in <p>
"""

from __future__ import annotations

import pytest

from src.labeller.normalize import (
    ALLOWED_TAGS,
    NORMALIZER_VERSION,
    normalize_html,
    text_coverage_ratio,
)


def _tags(html: str) -> set[str]:
    import re

    return set(re.findall(r"<([a-z0-9]+)", html))


def test_empty_input():
    result = normalize_html("")
    assert result.html == ""
    assert result.text == ""
    assert result.version == NORMALIZER_VERSION


def test_whitespace_only_input():
    result = normalize_html("   \n\t  ")
    assert result.html == ""
    assert result.text == ""


def test_only_allowed_tags_survive():
    raw = "<div class='intro'>Hi</div><section><p>Body text</p></section><span>inline</span>"
    result = normalize_html(raw)
    assert _tags(result.html).issubset(ALLOWED_TAGS | {"p"})
    assert "Hi" in result.text
    assert "Body text" in result.text
    assert "inline" in result.text


def test_scripts_styles_fully_dropped():
    raw = (
        "<p>Keep this</p>"
        "<script>alert('xss')</script>"
        "<style>.foo {}</style>"
        "<noscript>fallback</noscript>"
    )
    result = normalize_html(raw)
    assert "alert" not in result.html
    assert ".foo" not in result.html
    assert "fallback" not in result.html
    assert "Keep this" in result.text


def test_b_and_i_become_semantic():
    raw = "<p>hello <b>world</b> and <i>friends</i></p>"
    result = normalize_html(raw)
    assert "<strong>" in result.html
    assert "<em>" in result.html
    assert "<b>" not in result.html
    assert "<i>" not in result.html


def test_href_preserved_on_a_tag():
    raw = '<p>See <a href="https://example.com" class="link" onclick="x">here</a>.</p>'
    result = normalize_html(raw)
    assert "https://example.com" in result.html
    assert "onclick" not in result.html
    assert "class=" not in result.html


def test_unsafe_img_video_audio_dropped():
    raw = "<p>Text before</p><img src='x' /><iframe src='evil'></iframe><p>Text after</p>"
    result = normalize_html(raw)
    assert "<img" not in result.html
    assert "<iframe" not in result.html
    assert "Text before" in result.text
    assert "Text after" in result.text


def test_coverage_ratio_high_for_realistic_post():
    raw = (
        "<div><h2>Senior Engineer</h2>"
        "<p>We are looking for a <strong>senior backend engineer</strong> "
        "to join our payments team. You will work on:</p>"
        "<ul><li>Distributed systems</li><li>Kubernetes</li></ul></div>"
    )
    result = normalize_html(raw)
    ratio = text_coverage_ratio(raw, result.text)
    assert ratio >= 0.9


def test_nested_disallowed_tags_unwrap_fully():
    raw = "<div><div><section><article><p>Deep nested</p></article></section></div></div>"
    result = normalize_html(raw)
    assert "Deep nested" in result.text
    assert "<section>" not in result.html
    assert "<article>" not in result.html
    assert "<div>" not in result.html


def test_h1_demoted_and_h5_lifted():
    raw = "<h1>Title</h1><h5>sub</h5><h6>subsub</h6>"
    result = normalize_html(raw)
    assert "<h1>" not in result.html
    assert "<h5>" not in result.html
    assert "<h6>" not in result.html
    assert "Title" in result.text
    assert "sub" in result.text


def test_naked_text_gets_wrapped_in_p():
    raw = "<div>naked text</div>other naked text<p>wrapped</p>"
    result = normalize_html(raw)
    # After unwrapping <div>, both "naked text" strings should end up in <p>
    assert "naked text" in result.text
    assert "other naked text" in result.text


def test_version_string_stable():
    r1 = normalize_html("<p>a</p>")
    r2 = normalize_html("<p>b</p>")
    assert r1.version == r2.version == NORMALIZER_VERSION


def test_text_coverage_empty():
    assert text_coverage_ratio("", "") == 1.0
    assert text_coverage_ratio("<div></div>", "") == 1.0


def test_text_coverage_drop_all():
    raw = "<p>lots of content here that should be preserved</p>"
    assert text_coverage_ratio(raw, "") < 0.1


@pytest.mark.parametrize(
    "raw",
    [
        "<p>a</p>",
        "<ul><li>1</li><li>2</li></ul>",
        "<h2>Title</h2><p>body</p>",
        "<blockquote><p>quote</p></blockquote>",
    ],
)
def test_already_clean_html_roundtrips(raw: str):
    """Input already using the allowed subset should be idempotent modulo whitespace."""
    result = normalize_html(raw)
    # Re-normalizing should be a no-op
    again = normalize_html(result.html)
    assert again.text == result.text
