from __future__ import annotations

from pathlib import Path

import pytest

CRAWLER_ROOT = Path(__file__).resolve().parents[1]

MIGRATED_MONITORS = {
    "src/core/monitors/hireology.py": "fetch_json_page_with_retry",
    "src/core/monitors/lever.py": "fetch_json_page_with_retry",
    "src/core/monitors/smartrecruiters.py": "fetch_json_page_with_retry",
    "src/core/monitors/umantis.py": "fetch_text_page_with_retry",
    "src/core/monitors/workday.py": "fetch_json_page_with_retry",
}


@pytest.mark.parametrize(
    ("relative_path", "helper_name"),
    MIGRATED_MONITORS.items(),
    ids=MIGRATED_MONITORS.keys(),
)
def test_migrated_monitors_delegate_page_retries_to_shared_helper(
    relative_path: str, helper_name: str
) -> None:
    source = (CRAWLER_ROOT / relative_path).read_text()

    assert helper_name in source
    assert "for attempt in range(retries)" not in source
    assert "is_retryable_status" not in source
    assert "http_retry_attempts_total" not in source
    assert "random.random()" not in source
