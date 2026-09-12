"""Regression coverage for MSD's centralized and Japan graduate boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx

from src.core.monitors.inline import discover

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _boards() -> dict[str, dict[str, str]]:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        return {
            row["board_slug"]: row for row in csv.DictReader(handle) if row["company_slug"] == "msd"
        }


def test_msd_uses_centralized_workday_and_distinct_japan_graduate_board() -> None:
    boards = _boards()

    assert set(boards) == {"msd-careers", "msd-japan-new-grads"}
    assert boards["msd-careers"]["board_url"] == ("https://msd.wd5.myworkdayjobs.com/searchjobs")
    assert boards["msd-careers"]["monitor_type"] == "workday"
    assert boards["msd-careers"]["scraper_type"] == "workday"


async def test_japan_graduate_board_extracts_hidden_role_panels_with_real_locations() -> None:
    row = _boards()["msd-japan-new-grads"]
    board = {
        "board_url": row["board_url"],
        "metadata": json.loads(row["monitor_config"]),
    }
    html = """
    <h2>募集要項</h2>
    <h3>MR職</h3>
    <p>医療用医薬品の情報を医療関係者に提供する職種です。</p>
    <p>勤務地</p><p>全国</p>
    <h2>お仕事体験プログラム</h2>
    <h3>MR職 よくあるご質問</h3><p>採用に関する回答です。</p>
    <div hidden>
      <h3>メディカルアフェアーズ職</h3>
      <p>医学・科学の専門家として関係者をつなぐ職種です。</p>
      <p>勤務地</p><p>本社（東京）</p>
      <h3>生産本部職</h3>
      <p>医薬品の製造と品質管理を担う職種です。</p>
      <p>勤務地</p><p>埼玉県</p>
      <h3>開発職</h3>
      <p>臨床試験を計画・実施・管理する職種です。</p>
      <p>勤務地</p><p>本社（東京） ＊変更の範囲：大阪オフィス</p>
      <h3>統計職</h3>
      <p>臨床試験の統計解析を担う職種です。</p>
      <p>勤務地</p><p>本社（東京）</p>
    </div>
    <h2>もっとMSDを知る</h2>
    <h3>会社について</h3><p>会社案内です。</p>
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    async with httpx.AsyncClient(transport=transport) as client:
        jobs = await discover(board, client)

    by_title = {job.title: job for job in jobs}
    assert set(by_title) == {
        "MR職",
        "メディカルアフェアーズ職",
        "生産本部職",
        "開発職",
        "統計職",
    }
    assert by_title["MR職"].locations == ["全国"]
    assert by_title["生産本部職"].locations == ["埼玉県"]
    assert by_title["開発職"].locations == ["本社（東京） ＊変更の範囲：大阪オフィス"]
    assert all(job.description and job.employment_type == "full_time" for job in jobs)
