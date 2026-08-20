from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitors import all_monitor_types, monitor_needs_browser
from src.core.monitors.candidatus import (
    _canonical_detail_url,
    _card_indexes,
    _discover_page,
    _is_listing_url,
    can_handle,
)
from src.core.scrapers.dom import parse_html
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.commands.help import MONITOR_CARDS

BOARD_URL = "https://carrieres.candidatus.com/site-emploi,MTE0OzA7MTE0"


def _listing(*indexes: int) -> str:
    cards = "".join(
        (
            f'<a id="c-{index}-A20" '
            f'href="javascript:_PAGE_.A18.value={index};'
            "if(clWDUtil.pfGetTraitement('RECRUTEMENT_LISTEANNONCES_XMOD10',18,void 0)())"
            f"{{_JSL(_PAGE_,'A20','_self','','');}}\">Role {index}</a>"
        )
        for index in indexes
    )
    return f"<html><body>{cards}</body></html>"


def _detail(index: int) -> str:
    return f"https://carrieres.candidatus.com/annonce-emploi,tenant-{index},role-{index}"


class _FakeLocator:
    def __init__(self, page: _FakePage, index: int):
        self.page = page
        self.index = index
        self.first = self

    async def count(self) -> int:
        return int(self.index in self.page.indexes)

    async def click(self) -> None:
        self.page.url = self.page.destinations[self.index]


class _FakePage:
    def __init__(self, indexes: list[int], destinations: dict[int, str] | None = None):
        self.indexes = indexes
        self.destinations = destinations or {index: _detail(index) for index in indexes}
        self.url = BOARD_URL

    def locator(self, selector: str) -> _FakeLocator:
        match = __import__("re").fullmatch(r"#c-(\d+)-A20", selector)
        assert match is not None
        return _FakeLocator(self, int(match.group(1)))

    async def wait_for_url(self, predicate, **_kwargs) -> None:
        if not predicate(self.url):
            raise TimeoutError(self.url)

    async def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None


def test_validates_listing_and_detail_urls() -> None:
    assert _is_listing_url(BOARD_URL)
    assert detect_ats_from_url(BOARD_URL) == "candidatus"
    assert not _is_listing_url("http://carrieres.candidatus.com/site-emploi,abc")
    assert not _is_listing_url("https://carrieres.candidatus.com/annonce-emploi,x,y")
    assert _canonical_detail_url(f"{_detail(1)}?source=test#apply") == _detail(1)
    assert _canonical_detail_url("https://evil.example/annonce-emploi,x,y") is None


def test_extracts_only_consistent_windev_cards() -> None:
    html = _listing(1, 2).replace(
        "</body>",
        '<a id="c-3-A20" href="javascript:_PAGE_.A18.value=4;">Mismatch</a></body>',
    )
    assert _card_indexes(html) == [1, 2]
    with pytest.raises(RuntimeError, match="malformed WinDev job-title control"):
        _card_indexes(html, strict=True)
    assert _card_indexes("<html>ordinary links</html>") == []


async def test_can_handle_counts_cards_and_returns_browser_config() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=_listing(1, 2, 3), request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        config = await can_handle(BOARD_URL, client)

    assert config == {"wait": "domcontentloaded", "timeout": 30_000, "jobs": 3}
    assert "candidatus" in all_monitor_types()
    assert "candidatus" in MONITOR_CARDS
    assert monitor_needs_browser("candidatus", config)


async def test_resolves_every_postback_and_checks_listing_stability() -> None:
    page = _FakePage([1, 2, 3])

    async def fake_navigate(target, url: str, _config: dict) -> None:
        target.url = url

    with (
        patch("src.core.monitors.candidatus.navigate", side_effect=fake_navigate),
        patch(
            "src.core.monitors.candidatus.safe_content",
            new_callable=AsyncMock,
            side_effect=lambda _page: _listing(*_page.indexes),
        ),
    ):
        urls = await _discover_page(page, BOARD_URL, {})

    assert urls == {_detail(1), _detail(2), _detail(3)}


async def test_duplicate_detail_url_fails_closed() -> None:
    page = _FakePage([1, 2], {1: _detail(1), 2: _detail(1)})
    with (
        patch("src.core.monitors.candidatus.navigate", new_callable=AsyncMock),
        patch(
            "src.core.monitors.candidatus.safe_content",
            new_callable=AsyncMock,
            return_value=_listing(1, 2),
        ),
        pytest.raises(RuntimeError, match="duplicate URL"),
    ):
        await _discover_page(page, BOARD_URL, {})


async def test_malformed_listing_control_fails_closed() -> None:
    page = _FakePage([1, 2])
    malformed = _listing(1, 2).replace("_PAGE_.A18.value=2", "_PAGE_.A18.value=3")
    with (
        patch("src.core.monitors.candidatus.navigate", new_callable=AsyncMock),
        patch(
            "src.core.monitors.candidatus.safe_content",
            new_callable=AsyncMock,
            return_value=malformed,
        ),
        pytest.raises(RuntimeError, match="malformed WinDev job-title control"),
    ):
        await _discover_page(page, BOARD_URL, {})


def test_auto_scraper_extracts_candidatus_detail_fields() -> None:
    scraper = auto_scraper_type("candidatus")
    assert scraper is not None
    scraper_type, config = scraper
    assert scraper_type == "dom"
    assert config is not None
    html = """
      <html><body>
        <table><tr><td id="tzA3">Responsable de production H/F</td></tr></table>
        <table><tr><td id="tzA7">27400 Louviers</td></tr></table>
        <div class="uniform-text hidden-before-ready">
          <div>Présentation de Fresenius Kabi.</div>
          <div>Vos missions :</div><div>Diriger les équipes de production.</div>
        </div>
        <div>Hosted by Candidatus.com</div>
      </body></html>
    """
    content = parse_html(html, config)
    assert content.title == "Responsable de production H/F"
    assert content.locations == ["27400 Louviers"]
    assert "Diriger les équipes" in (content.description or "")
