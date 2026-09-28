from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[3] / "scripts/repair-swisslog-description-locales.py"
_SPEC = importlib.util.spec_from_file_location("swisslog_locale_repair", _PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def fixture():
    row = dict(
        company_id="company",
        source_url="source",
        is_active=True,
        scrape_is_leased=False,
        locales=["en"],
    )
    body = dict(locale="de", html="<p>Full body</p>", r2_uploaded=True)
    expected = dict(
        source_url="source",
        old_locales=["en"],
        locale="de",
        html_sha256=hashlib.sha256(body["html"].encode()).hexdigest(),
    )
    return row, body, expected


def test_verified_body_repairs_locale_and_repeat_is_noop():
    row, body, expected = fixture()
    assert _MODULE.validate_posting(row, [body], expected, "company")
    assert not _MODULE.validate_posting({**row, "locales": ["de"]}, [body], expected, "company")


@pytest.mark.parametrize(
    "change",
    [
        {"company_id": "other"},
        {"source_url": "other"},
        {"is_active": False},
        {"scrape_is_leased": True},
        {"locales": ["fr"]},
    ],
)
def test_changed_posting_is_rejected(change):
    row, body, expected = fixture()
    with pytest.raises(ValueError):
        _MODULE.validate_posting({**row, **change}, [body], expected, "company")


@pytest.mark.parametrize(
    "change",
    [
        {"locale": "sv"},
        {"html": "changed"},
        {"r2_uploaded": False},
        {"r2_uploaded": None},
    ],
)
def test_changed_or_unuploaded_body_is_rejected(change):
    row, body, expected = fixture()
    with pytest.raises(ValueError):
        _MODULE.validate_posting(row, [{**body, **change}], expected, "company")


def test_ambiguous_or_missing_description_is_rejected():
    row, body, expected = fixture()
    for bodies in [[], [body, body]]:
        with pytest.raises(ValueError):
            _MODULE.validate_posting(row, bodies, expected, "company")
