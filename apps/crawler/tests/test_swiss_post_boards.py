"""Identity split contracts for Swiss Post and PostFinance boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.core.monitor import MonitorResult, _apply_job_filter
from src.core.monitors import DiscoveredJob

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_POSTFINANCE_PATTERN = "(?i)PostFinance"


def _company_rows(company_slug: str) -> list[dict[str, str]]:
    with _BOARDS_PATH.open(newline="") as handle:
        return [
            row
            for row in csv.DictReader(handle)
            if row["company_slug"] == company_slug
        ]


def test_registry_uses_one_complementary_feed_board_per_company():
    swiss_post = _company_rows("swiss-post")
    postfinance = _company_rows("postfinance")

    assert len(swiss_post) == len(postfinance) == 1
    swiss_post_config = json.loads(swiss_post[0]["monitor_config"])
    postfinance_config = json.loads(postfinance[0]["monitor_config"])

    assert swiss_post[0]["monitor_type"] == postfinance[0]["monitor_type"] == "rss"
    assert swiss_post_config["job_filter"] == {"exclude": _POSTFINANCE_PATTERN}
    assert postfinance_config["job_filter"] == {"include": _POSTFINANCE_PATTERN}
    assert swiss_post_config["feed_url"] == "https://job.post.ch/googlefeed.xml"
    assert postfinance_config["feed_url"] == "https://jobs.postfinance.ch/googlefeed.xml"
    assert postfinance[0]["board_url"].startswith("https://jobs.postfinance.ch/")


def test_complementary_filters_partition_rich_jobs_without_overlap():
    swiss_url = "https://job.post.ch/PostKG/job/Bern-Engineer/1/"
    postfinance_url = "https://job.post.ch/PostFinance/job/Bern-Analyst/2/"
    jobs = {
        swiss_url: DiscoveredJob(
            url=swiss_url,
            title="Engineer",
            description="Build logistics systems",
            metadata={"employer": "Post CH AG"},
        ),
        postfinance_url: DiscoveredJob(
            url=postfinance_url,
            title="Analyst",
            description="Support PostFinance customers",
            metadata={"employer": "PostFinance AG"},
        ),
    }
    source = MonitorResult(urls=set(jobs), jobs_by_url=jobs)

    swiss_post = _apply_job_filter(
        source,
        {"job_filter": {"exclude": _POSTFINANCE_PATTERN}},
    )
    postfinance = _apply_job_filter(
        source,
        {"job_filter": {"include": _POSTFINANCE_PATTERN}},
    )

    assert swiss_post.urls == {swiss_url}
    assert postfinance.urls == {postfinance_url}
    assert not swiss_post.urls & postfinance.urls
    assert swiss_post.urls | postfinance.urls == source.urls
