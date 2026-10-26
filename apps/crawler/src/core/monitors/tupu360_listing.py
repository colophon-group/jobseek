"""Static Tupu360 listing support for the DOM monitor."""

from __future__ import annotations

import re
from urllib.parse import urlencode, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import DiscoveredJob
from src.shared.http_retry import fetch_text_page_with_retry

_MAX_JOBS = 5_000
_PAGE_SIZE = 100
_ID = re.compile(r"[a-f0-9]{24}")
_PATH = re.compile(r"/[a-z0-9_-]+/position/index", re.IGNORECASE)
_RECRUITMENT = re.compile(r"[A-Z]{1,32}RECRUITMENT")


def is_listing_url(url: str) -> bool:
    """Accept only unfiltered public provider listing roots."""
    try:
        parts = urlsplit(url)
        return bool(
            parts.scheme == "https"
            and (parts.hostname or "").endswith(".tupu360.com")
            and parts.username is None
            and parts.password is None
            and parts.port is None
            and not parts.query
            and not parts.fragment
            and _PATH.fullmatch(parts.path)
        )
    except ValueError:
        return False


def parse_page(html: str, board_url: str) -> tuple[int, list[DiscoveredJob]]:
    """Parse the authoritative count and every card, failing on layout drift."""
    if not is_listing_url(board_url):
        raise ValueError("Tupu360 requires an unfiltered public listing URL")
    tree = LexborHTMLParser(html)
    markers = tree.css("#position-list[positioncount]")
    if len(markers) != 1:
        raise ValueError("Tupu360 listing omitted its authoritative count")
    count = markers[0].attributes["positioncount"]
    if not re.fullmatch(r"[0-9]{1,5}", count) or int(count) > _MAX_JOBS:
        raise ValueError("Tupu360 listing count exceeds its bound")
    total = int(count)
    jobs = []
    for row in tree.css("#position-list .position-item"):
        position_id = row.attributes.get("pid", "")
        recruitment = row.attributes.get("recruitment", "")
        title = row.css_first(".position-name .title .txt")
        location = row.css_first(".e-city .txt")
        posted = row.css_first(".time")
        if (
            _ID.fullmatch(position_id) is None
            or _RECRUITMENT.fullmatch(recruitment) is None
            or title is None
            or location is None
            or posted is None
            or row.attributes.get("projectid")
        ):
            raise ValueError("Tupu360 listing card omitted a required field")
        title_text = " ".join(title.text(strip=True).split())
        location_match = re.fullmatch(r"工作城市[：:]\s*(.+)", location.text(strip=True))
        date_match = re.fullmatch(r"发布于[：:]\s*(\d{4}-\d{2}-\d{2})", posted.text(strip=True))
        if not title_text or location_match is None or date_match is None:
            raise ValueError("Tupu360 listing card has invalid title, location or date")
        query = urlencode({"positionId": position_id, "recruitmentType": recruitment})
        salary_node = row.css_first(".e-salary .txt")
        salary_text = salary_node.text(strip=True) if salary_node is not None else ""
        salary_match = re.fullmatch(r"薪资待遇[：:]\s*([0-9]+)-([0-9]+)", salary_text)
        salary = None
        if salary_match is not None:
            lower, upper = int(salary_match[1]), int(salary_match[2])
            if lower > upper:
                raise ValueError("Tupu360 listing salary range is reversed")
            # The cards do not label currency or period for numeric ranges.
            salary = {"min": lower, "max": upper}
        jobs.append(
            DiscoveredJob(
                url=f"{board_url.removesuffix('index')}detail?{query}",
                title=title_text,
                locations=[location_match[1].strip()],
                date_posted=date_match[1],
                base_salary=salary,
                language="zh",
                metadata={
                    "position_id": position_id,
                    "recruitment_type": recruitment,
                    "salary_text": salary_text,
                },
            )
        )
    if len(jobs) > _PAGE_SIZE or len(jobs) > total:
        raise ValueError("Tupu360 listing rows exceed the advertised count or page size")
    return total, jobs


async def discover(board_url: str, client: httpx.AsyncClient) -> list[DiscoveredJob]:
    """Reconcile offset pages with an unchanged exact total before emitting jobs."""
    if not is_listing_url(board_url):
        raise ValueError("Tupu360 requires an unfiltered public listing URL")
    expected_total = None
    jobs: dict[str, DiscoveredJob] = {}
    for _ in range(_MAX_JOBS):
        page_url = str(
            httpx.URL(board_url).copy_merge_params({"offset": len(jobs), "max": _PAGE_SIZE})
        )
        html = await fetch_text_page_with_retry(
            client,
            page_url,
            end_of_pagination_statuses=(),
            require_nonempty=True,
            max_bytes=2 * 1024 * 1024,
        )
        total, page_jobs = parse_page(html or "", board_url)
        if expected_total is None:
            expected_total = total
        if total != expected_total:
            raise ValueError("Tupu360 listing total changed during pagination")
        if not page_jobs and total:
            raise ValueError("Tupu360 listing ended before its advertised total")
        for job in page_jobs:
            position_id = (job.metadata or {})["position_id"]
            if position_id in jobs:
                raise ValueError("Tupu360 listing repeated a position during pagination")
            jobs[position_id] = job
        if len(jobs) == total:
            return list(jobs.values())
        if len(jobs) > total:
            raise ValueError("Tupu360 listing exceeded its advertised total")
    raise ValueError("Tupu360 listing exceeded its pagination bound")
