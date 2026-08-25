from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import parse_qsl

import httpx
import pytest

from src.core.monitors import _build_comment, all_monitor_types
from src.core.monitors import infoniqa as monitor
from src.redis_queue import _KNOWN_ATS_DOMAIN_SUFFIXES
from src.workspace._compat import detect_ats_from_url
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

BOARD_URL = (
    "https://ehlcampus.infoniqa.io/hcm/jobexchange/showJobOfferList.do?init=true&j=jobexchange"
)
LIST_URL = "https://ehlcampus.infoniqa.io/hcm/jobexchange/showJobOfferList.do"
EMPLOYER = "EHL Hotelfachschule Passugg"
CSRF = "6526e5df-bf9f-4d16-9c2f-0ed3067c4ab6"
JOB_IDS = (
    "8a7ec04b955ee2d701956b4405230214",
    "8a7ec1119f2fcb33019f37afb5e00079",
)


def _shell(*, employer: str = EMPLOYER, quicksearch_host: str = "") -> str:
    quicksearch = (
        f"{quicksearch_host}/hcm/jobexchange/showJobOfferList.do?j=jobexchange"
        if quicksearch_host
        else "/hcm/jobexchange/showJobOfferList.do?j=jobexchange"
    )
    return f"""
    <html>
      <body class="jobOfferList">
        <a class="navbar-brand"><img
          src="/hcm/jobexchange/streamResource.do/1/styles/jobexchange/theme/images/logo.svg"
          alt="{employer} Logo"></a>
        <a class="menu menuQuicksearch" href="{quicksearch}">Offene Jobs</a>
        <h1 class="caption">Stellenangebote der {employer}</h1>
        <form id="jobOfferSearch" method="post" action="">
          <input name="j" type="hidden" value="jobexchange">
          <input name="_csrf" type="hidden" value="{CSRF}">
        </form>
        <script>
          $.ajax({{url: '/hcm/jobexchange/showJobOfferList.do?search=true',
                    data: {{'_csrf': '{CSRF}'}}}});
          $.ajax({{data: {{showNextJobOffers: 'true', '_csrf': '{CSRF}'}}}});
          $.ajax({{data: {{hasNextJobOffers: 'true', '_csrf': '{CSRF}'}}}});
        </script>
      </body>
    </html>
    """


def _job(job_id: str, title: str = "Sales Officer") -> str:
    target = f"showJobOfferDetail.do?jobOfferId={job_id}&amp;j=jobexchange&amp;organizationUnitId="
    return f"""
    <li class="jobOffer" tabindex="0"
        onclick="showBlockUIMessage(window,'wait');window.location.href='{target}'"
        onkeydown="if(event.key === 'Enter') {{window.location.href='{target}'}}">
      <div><h2 class="jobOfferDescription">{title}</h2></div>
      <ul><li class="fieldHeader">Entry date</li></ul>
    </li>
    """


def _search(total: int, *, button_total: int | None = None, jobs: str = "") -> str:
    button_total = total if button_total is None else button_total
    return f"""
    <script>$('#showJobsButton').val('Jobs anzeigen ({button_total})');</script>
    <p class="searchResultInfo">{total} Treffer zu Ihrer Suche</p>
    <ul id="jobOffers" class="jobOffers">{jobs}</ul>
    <span id="showNextJobOffers"></span>
    """


class _Protocol:
    def __init__(
        self,
        *,
        shell: str | None = None,
        search: str | None = None,
        pages: Sequence[str] = (),
        has_next: Sequence[bool] | None = None,
    ) -> None:
        self.shell = shell or _shell()
        self.search = search or _search(len(pages))
        self.pages = list(pages)
        self.has_next = list(has_next or ([True] * len(pages) + [False]))
        self.calls: list[tuple[str, str, list[tuple[str, str]]]] = []
        self.page_index = 0
        self.has_next_index = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = (await request.aread()).decode()
        form = parse_qsl(body, keep_blank_values=True)
        self.calls.append((request.method, str(request.url), form))
        headers = {"content-type": "text/html;charset=UTF-8"}
        if request.method == "GET":
            assert str(request.url) == BOARD_URL
            return httpx.Response(
                200,
                text=self.shell,
                headers={**headers, "set-cookie": "JSESSIONID=test-session; Path=/hcm"},
            )

        assert request.headers["cookie"] == "JSESSIONID=test-session"
        assert request.headers["origin"] == "https://ehlcampus.infoniqa.io"
        assert request.headers["referer"] == BOARD_URL
        assert request.headers["x-requested-with"] == "XMLHttpRequest"
        if str(request.url) == f"{LIST_URL}?search=true":
            assert form == [("j", "jobexchange"), ("_csrf", CSRF)]
            return httpx.Response(200, text=self.search, headers=headers)
        if form and form[0] == ("hasNextJobOffers", "true"):
            assert form == [("hasNextJobOffers", "true"), ("_csrf", CSRF)]
            value = self.has_next[self.has_next_index]
            self.has_next_index += 1
            return httpx.Response(
                200,
                text="true" if value else "false",
                headers={"content-type": "application/json"},
            )
        assert form == [
            ("showNextJobOffers", "true"),
            ("j", "jobexchange"),
            ("_csrf", CSRF),
        ]
        page = self.pages[self.page_index]
        self.page_index += 1
        return httpx.Response(200, text=page, headers=headers)


async def _discover(protocol: _Protocol, *, employer: str = EMPLOYER) -> set[str]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(protocol)) as client:
        return await monitor.discover(
            {"board_url": BOARD_URL, "metadata": {"employer_name": employer}},
            client,
        )


@pytest.mark.asyncio
async def test_replays_live_post_flow_and_extracts_onclick_jobs() -> None:
    protocol = _Protocol(
        search=_search(2),
        pages=[_job(JOB_IDS[0], "Aushilfe") + _job(JOB_IDS[1], "Sales Officer")],
    )

    result = await _discover(protocol)

    assert result == {
        f"{LIST_URL.replace('showJobOfferList', 'showJobOfferDetail')}"
        f"?jobOfferId={job_id}&j=jobexchange&organizationUnitId="
        for job_id in JOB_IDS
    }
    assert [method for method, _url, _form in protocol.calls] == [
        "GET",
        "POST",
        "POST",
        "POST",
        "POST",
    ]


@pytest.mark.asyncio
async def test_accepts_zero_only_after_authoritative_search_and_has_next_false() -> None:
    protocol = _Protocol(search=_search(0), has_next=[False])

    assert await _discover(protocol) == set()
    assert len(protocol.calls) == 3


@pytest.mark.asyncio
async def test_pre_hydration_shell_cannot_masquerade_as_zero() -> None:
    protocol = _Protocol(search=_search(2), has_next=[False])

    with pytest.raises(ValueError, match="returned 0 unique jobs, expected 2"):
        await _discover(protocol)


@pytest.mark.asyncio
async def test_rejects_configured_employer_mismatch_before_search() -> None:
    protocol = _Protocol()

    with pytest.raises(ValueError, match="does not match the configured employer"):
        await _discover(protocol, employer="Attacker")
    assert len(protocol.calls) == 1


@pytest.mark.asyncio
async def test_rejects_wrong_origin_provider_identity() -> None:
    protocol = _Protocol(shell=_shell(quicksearch_host="https://attacker.example"))

    with pytest.raises(ValueError, match="quick-search link crossed board identity"):
        await _discover(protocol)
    assert len(protocol.calls) == 1


@pytest.mark.asyncio
async def test_rejects_conflicting_authoritative_counts() -> None:
    protocol = _Protocol(search=_search(2, button_total=0))

    with pytest.raises(ValueError, match="inconsistent result counts"):
        await _discover(protocol)


@pytest.mark.asyncio
async def test_rejects_cross_origin_onclick_target() -> None:
    malicious = _job(JOB_IDS[0]).replace(
        "showJobOfferDetail.do?",
        "https://attacker.example/hcm/jobexchange/showJobOfferDetail.do?",
    )
    protocol = _Protocol(search=_search(1), pages=[malicious])

    with pytest.raises(ValueError, match="malformed job row"):
        await _discover(protocol)


@pytest.mark.asyncio
async def test_rejects_repeated_pagination_jobs() -> None:
    protocol = _Protocol(
        search=_search(2),
        pages=[_job(JOB_IDS[0]), _job(JOB_IDS[0])],
    )

    with pytest.raises(ValueError, match="pagination repeated 1 jobs"):
        await _discover(protocol)


@pytest.mark.parametrize(
    "url",
    [
        BOARD_URL.replace("https://", "http://"),
        BOARD_URL.replace("ehlcampus.infoniqa.io", "attacker.example"),
        BOARD_URL.replace("ehlcampus.infoniqa.io", "infoniqa.io"),
        BOARD_URL + "&freeText=chef",
        BOARD_URL.replace("init=true", "init=false"),
        BOARD_URL.replace("showJobOfferList.do", "showJobOfferDetail.do"),
        BOARD_URL.replace("https://", "https://user@"),
    ],
)
def test_rejects_untrusted_or_filtered_board_urls(url: str) -> None:
    assert monitor._board_from_url(url) is None
    assert detect_ats_from_url(url) != "infoniqa"


@pytest.mark.asyncio
async def test_live_probe_reports_employer_and_job_count() -> None:
    protocol = _Protocol(search=_search(0), has_next=[False])
    async with httpx.AsyncClient(transport=httpx.MockTransport(protocol)) as client:
        result = await monitor.can_handle(BOARD_URL, client)

    assert result == {"employer_name": EMPLOYER, "jobs": 0}
    assert _build_comment("infoniqa", result) == (
        f"Infoniqa jobexchange — employer: {EMPLOYER}, 0 jobs"
    )


def test_runtime_registration_and_operator_surfaces() -> None:
    assert "infoniqa" in all_monitor_types()
    assert detect_ats_from_url(BOARD_URL) == "infoniqa"
    assert "infoniqa" in MONITOR_CARDS
    assert "infoniqa" in _MONITOR_CONFIG_HINTS
    assert ".infoniqa.io" in _KNOWN_ATS_DOMAIN_SUFFIXES
