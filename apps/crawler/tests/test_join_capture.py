"""JOIN replay capture observes existing fetched text without another GET."""

from __future__ import annotations

import pytest

from src.core.monitors import join_capture, nextdata


def test_capture_is_exact_bounded_and_exclusive(monkeypatch, tmp_path):
    monkeypatch.setattr(join_capture, "_CAPTURE_DIR", tmp_path)
    monkeypatch.setenv("JOIN_CAPTURE_SLUGS", "acme")
    join_capture.capture_join_text("https://join.com/companies/acme", "first")
    join_capture.capture_join_text("https://join.com/companies/acme?page=2", "second")
    join_capture.capture_join_text("https://join.com/companies/acme?page=2", "overwrite")
    join_capture.capture_join_text("https://join.com/companies/acme?page=5", "late")
    join_capture.capture_join_text("https://join.com/companies/other", "foreign")
    assert (tmp_path / "jobseek-join-acme-1.html").read_text() == "first"
    assert (tmp_path / "jobseek-join-acme-2.html").read_text() == "second"
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.asyncio
async def test_capture_observes_single_existing_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(join_capture, "_CAPTURE_DIR", tmp_path)
    monkeypatch.setenv("JOIN_CAPTURE_SLUGS", "acme")
    requests = []

    async def fetch(url, *_args, **_kwargs):
        requests.append(url)
        return "observed body"

    monkeypatch.setattr(nextdata, "fetch_page_text", fetch)
    result = await nextdata._fetch_html("https://join.com/companies/acme", False, None)
    assert result == "observed body"
    assert requests == ["https://join.com/companies/acme"]
    assert (tmp_path / "jobseek-join-acme-1.html").read_text() == result
