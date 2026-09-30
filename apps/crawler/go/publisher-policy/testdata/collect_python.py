"""Freeze the existing resource-level Python parser; no origin traffic."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.shared.tdm import TDMReservedError, check_browser_response

cases = [
    ("none", {}, "<html></html>"),
    ("header", {"tdm-reservation": " 1 ", "tdm-policy": "header-license"}, ""),
    ("header_zero", {"tdm-reservation": "0"}, ""),
    ("invalid_literal", {"tdm-reservation": "01"}, ""),
    ("extra_attrs", {}, '<META data-x="y" NAME="TDM-RESERVATION" CONTENT="1">'),
    ("opt_in", {"tdm-reservation": "1"}, '<meta name="tdm-reservation" content="0">'),
    ("opt_out", {"tdm-reservation": "0"}, '<meta name="tdm-reservation" content="1">'),
    (
        "policy_override",
        {"tdm-reservation": "1", "tdm-policy": "header"},
        '<meta name="tdm-policy" content="meta">',
    ),
    (
        "empty_policy_fallback",
        {"tdm-reservation": "1", "tdm-policy": "header"},
        '<meta name="tdm-policy" content="meta"><meta name="tdm-policy" content="">',
    ),
    ("invalid_meta", {"tdm-reservation": "1"}, '<meta name="tdm-reservation" content="false">'),
    (
        "last_valid",
        {},
        '<meta name="tdm-reservation" content="1"><meta name="tdm-reservation" content="0">',
    ),
    (
        "invalid_after_valid",
        {},
        '<meta name="tdm-reservation" content="1"><meta name="tdm-reservation" content="bad">',
    ),
    (
        "literal_script",
        {},
        '<script>const x=\'<meta name="tdm-reservation" content="1">\';</script>',
    ),
    (
        "literal_style",
        {},
        '<style>p:before{content:\'<meta name="tdm-reservation" content="1">\'}</style>',
    ),
    ("literal_comment", {}, '<!-- <meta name="tdm-reservation" content="1"> -->'),
    (
        "entities",
        {},
        '<meta name="tdm-reservation" content="&#49;"><meta name="tdm-policy" content="a&amp;b">',
    ),
    ("duplicates", {}, '<meta name="ignored" name="tdm-reservation" content="0" content="1">'),
    ("python_whitespace", {}, '<meta name="tdm-reservation" content="\x1c1\x1f">'),
    ("unicode_inside_bound", {}, "東" * 65000 + '<meta name="tdm-reservation" content="1">'),
    ("unicode_outside_bound", {}, "東" * 65536 + '<meta name="tdm-reservation" content="1">'),
    ("unfinished_bound", {}, "a" * 65510 + '<meta name="tdm-reservation" content="1">'),
]
rows = []
for name, headers, body in cases:
    expected = None
    try:
        check_browser_response(headers, body, url="https://publisher.invalid/job")
    except TDMReservedError as error:
        expected = {"url": error.url, "source": error.source, "policy_url": error.policy_url}
    rows.append({"name": name, "headers": headers, "body": body, "expected": expected})
Path(__file__).with_name("python_cases.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
)
