"""Tests for the block extractor."""

from __future__ import annotations

from src.labeller.blocks import extract_blocks
from src.labeller.normalize import normalize_html


def test_empty_input():
    assert extract_blocks("") == []
    assert extract_blocks("   ") == []


def test_simple_paragraphs_each_become_blocks():
    html = "<p>one</p>\n<p>two</p>\n<p>three</p>"
    blocks = extract_blocks(html)
    assert len(blocks) == 3
    assert [b.id for b in blocks] == [0, 1, 2]
    assert [b.tag for b in blocks] == ["p", "p", "p"]
    assert blocks[0].text == "one"
    assert blocks[2].text == "three"


def test_list_is_one_block_not_three():
    html = "<ul><li>a</li><li>b</li><li>c</li></ul>"
    blocks = extract_blocks(html)
    assert len(blocks) == 1
    assert blocks[0].tag == "ul"
    assert "a" in blocks[0].text
    assert "c" in blocks[0].text


def test_mix_of_headings_paragraphs_lists():
    html = (
        "<h2>About</h2>"
        "<p>We are Stripe.</p>"
        "<h3>Team</h3>"
        "<p>Payments platform.</p>"
        "<ul><li>one</li><li>two</li></ul>"
    )
    blocks = extract_blocks(html)
    assert len(blocks) == 5
    assert [b.tag for b in blocks] == ["h2", "p", "h3", "p", "ul"]


def test_ids_are_stable_and_contiguous():
    html = "<p>a</p><p>b</p><p>c</p>"
    blocks = extract_blocks(html)
    assert [b.id for b in blocks] == [0, 1, 2]


def test_empty_blocks_skipped():
    html = "<p></p><p>real content</p><p>   </p>"
    blocks = extract_blocks(html)
    assert len(blocks) == 1
    assert blocks[0].text == "real content"


def test_blocks_inherit_inline_formatting_in_html():
    html = "<p>hello <strong>world</strong></p>"
    blocks = extract_blocks(html)
    assert len(blocks) == 1
    assert "<strong>" in blocks[0].html
    assert blocks[0].text == "hello world"


def test_list_with_inline_markup_inside_bullets_stays_on_one_line_per_bullet():
    """Regression for pitfall C — <strong>/<br> inside <li> must not split mid-bullet."""
    html = (
        "<ul>"
        "<li><strong>Strategic Vision</strong>: Develop and execute the GRC strategy.</li>"
        "<li>Enterprise Risk<br>Oversee the risk program across functions.</li>"
        "<li>Plain bullet</li>"
        "</ul>"
    )
    blocks = extract_blocks(html)
    assert len(blocks) == 1
    lines = blocks[0].text.split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("Strategic Vision")
    assert "Strategic Vision : Develop" in lines[0] or "Strategic Vision: Develop" in lines[0]
    assert lines[1].startswith("Enterprise Risk")
    assert "Oversee" in lines[1]
    assert lines[2] == "Plain bullet"


def test_normalize_then_blocks_integration():
    raw = (
        "<div><h2>Senior Engineer</h2>"
        "<p>Build <b>distributed systems</b>.</p>"
        "<ul><li>Python</li><li>Go</li></ul></div>"
    )
    normalized = normalize_html(raw)
    blocks = extract_blocks(normalized.html)
    assert len(blocks) >= 3
    tags = [b.tag for b in blocks]
    assert "h2" in tags
    assert "p" in tags
    assert "ul" in tags
