from __future__ import annotations

import pytest

from src.processing.board import _classify_job_url

BOARD = "https://recruit.midea.com/recruitOut/ihr/home/index"
POSTING_ID = "1234567890abcdef1234567890abcdef"


def test_midea_posting_fragment_retains_its_identity():
    for posting_id in (POSTING_ID, "abcdef1234567890abcdef1234567890"):
        assert _classify_job_url(f"{BOARD}#/positionDetail/{posting_id}", BOARD) is None


@pytest.mark.parametrize(
    "fragment",
    [
        "0",
        "",
        "/positionDetail/",
        "/positionDetail/" + POSTING_ID[:-1],
        "/positionDetail/" + POSTING_ID.upper(),
        "/positionDetail/" + POSTING_ID + "/",
        "/positionDetail/" + POSTING_ID + "?other=1",
        "/other/" + POSTING_ID,
    ],
)
def test_midea_homepage_and_unverified_fragments_remain_rejected(fragment: str):
    assert _classify_job_url(f"{BOARD}#{fragment}", BOARD) == "board_homepage"


@pytest.mark.parametrize(
    "board",
    [
        BOARD.replace("https:", "http:"),
        BOARD.replace("recruit.midea.com", "example.com"),
        BOARD.replace("recruit.midea.com", "recruit.midea.com:443"),
        BOARD.replace("recruit.midea.com", "user:pass@recruit.midea.com"),
        BOARD.replace("/index", "/other"),
        BOARD + ";param",
        BOARD + "?listing=1",
        BOARD + "#listing",
    ],
)
def test_midea_fragment_cannot_authorize_another_listing(board: str):
    # Put the fragment on a same-path candidate so the original homepage guard
    # applies; only the exact trusted listing receives the new exception.
    base = board.split("#", 1)[0].split("?", 1)[0]
    assert _classify_job_url(f"{base}#/positionDetail/{POSTING_ID}", board) == "board_homepage"
