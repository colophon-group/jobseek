"""Jobs.cz DOM boards must discard volatile search tracking identities."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from src.core.monitor import MonitorResult, _apply_url_allowlist, _apply_url_transform
from src.core.monitors.dom import _validate_explicit_empty_states, _validated_empty_state_list

DATA_DIR = Path(__file__).parents[1] / "data"


def _config(board_slug: str) -> dict:
    with (DATA_DIR / "boards.csv").open(encoding="utf-8", newline="") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == board_slug)
    return json.loads(row["monitor_config"])


def test_jobs_cz_dom_boards_collapse_search_ids_to_one_stable_url() -> None:
    first = "https://www.jobs.cz/rpd/2001367181/?searchId=first&rps=233&lang=en"
    second = "https://www.jobs.cz/rpd/2001367181/?searchId=second&rps=233&lang=en"
    canonical = "https://www.jobs.cz/rpd/2001367181/"

    for board_slug in (
        "fedex-czechia-local",
        "ferring-pharmaceuticals-careers-cz",
    ):
        config = _config(board_slug)
        allowed = _apply_url_allowlist(MonitorResult(urls={first, second}), config)
        transformed = _apply_url_transform(allowed, config)

        assert transformed.urls == {canonical}
        assert re.fullmatch(config["url_allowlist"], first)


def test_jobs_cz_dom_boards_reject_non_provider_urls() -> None:
    hostile = "https://www.jobs.cz.attacker.example/rpd/2001367181/?searchId=x"
    for board_slug in (
        "fedex-czechia-local",
        "ferring-pharmaceuticals-careers-cz",
    ):
        allowed = _apply_url_allowlist(MonitorResult(urls={hostile}), _config(board_slug))
        assert allowed.urls == set()
        assert allowed.security_filtered_count == 1


def test_ferring_jobs_cz_authoritative_empty_state_matches_current_contract() -> None:
    board_url = "https://www.jobs.cz/prace/?company%5B%5D=2362438"
    html = """
    <div id="search-result-container">
      <div class="Alert Alert--informative">
        <span class="text--warning">
          Ferring-Léčiva, a.s. právě neobsazuje žádné pozice
        </span>
      </div>
      <div class="Stack Stack--hasIntermediateDividers"></div>
    </div>
    """
    empty_states = _validated_empty_state_list(
        _config("ferring-pharmaceuticals-careers-cz")["empty_states"]
    )

    _validate_explicit_empty_states(html, empty_states, set(), board_url)
