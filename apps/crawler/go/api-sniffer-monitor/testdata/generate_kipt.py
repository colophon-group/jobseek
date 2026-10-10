"""Freeze original KIPT bulletin lines, synthetic identities and dated inventories."""

from __future__ import annotations

import ast
import dataclasses
import json
import sys
from datetime import date
from pathlib import Path

import structlog

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from src.core.monitors import kipt  # noqa: E402

structlog.configure(logger_factory=structlog.ReturnLoggerFactory())
tree = ast.parse((root / "tests/test_kipt_monitor.py").read_text())
sample = next(
    ast.literal_eval(n.value)
    for n in tree.body
    if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "SAMPLE_BULLETIN" for t in n.targets)
)
pdf = "https://www.kipt.kharkov.ua/news/2026/vacancy_23_06_2026.pdf"
variants = {
    "complete": sample,
    "singular": sample.replace("вакантних посад:", "вакантної посади:"),
    "en-dash": sample.replace("- ", "– "),
    "upper": sample.upper(),
    "no-block": "Вимоги до кандидатів:\nNo vacancies.",
    "no-common": sample.split("Вимоги до кандидатів:")[0],
    "zero-count": sample.replace("1 вакансія", "0 вакансій"),
    "plural": sample.replace("1 вакансія", "12 вакансій"),
    "quotes": sample.replace(
        "заступника начальника відділу", "Заступника <відділу> & 'секції' \"фізики\""
    ),
    "multiline": sample.replace("начальника відділу", "начальника\nвідділу"),
}
cases = []
for name, text in variants.items():
    raw = pdf + "?x=first&x=second&_jid=old&blank=" if name == "quotes" else pdf
    jobs = [
        dataclasses.asdict(j)
        for j in kipt._parse_bulletin(raw, text, date(2026, 6, 23), "Kharkiv, Ukraine")
    ]
    for job in jobs:
        job["extras"] = {"language": job.pop("language")}
    cases.append(
        {
            "name": name,
            "url": raw,
            "text": text,
            "posted": "2026-06-23",
            "location": "Kharkiv, Ukraine",
            "jobs": jobs,
        }
    )
listing_cases = []
for name, today, age, links in [
    ("boundary", "2026-07-23", 30, ["../news/2026/vacancy_23_06_2026.pdf"]),
    ("expired", "2026-07-24", 30, ["../news/2026/vacancy_23_06_2026.pdf"]),
    ("future", "2026-06-22", 30, ["../news/2026/vacancy_23_06_2026.pdf"]),
    ("today", "2026-06-23", 0, ["../news/2026/vacancy_23_06_2026.pdf"]),
    ("invalid-date", "2026-07-23", 30, ["../news/2026/vacancy_31_06_2026.pdf"]),
    ("duplicates", "2026-07-23", 30, ["../news/2026/vacancy_23_06_2026.pdf"] * 2),
    ("non-bulletin", "2026-07-23", 30, ["../news/2026/admission_23_06_2026.pdf"]),
]:
    source = "".join(f'<a href="{link}">Vacancies</a>' for link in links)
    base = "https://www.kipt.kharkov.ua/ua/vacancy.html"
    bulletins = [
        {"URL": u, "Posted": d.isoformat()}
        for u, d in kipt._active_bulletins(
            base, source, today=date.fromisoformat(today), max_age_days=age
        )
    ]
    listing_cases.append(
        {
            "name": name,
            "source": source,
            "base": base,
            "today": today,
            "age": age,
            "bulletins": bulletins,
        }
    )
Path(__file__).with_name("python_kipt.json").write_text(
    json.dumps({"parser": cases, "listing": listing_cases}, indent=2, ensure_ascii=False) + "\n"
)
print(f"Original KIPT contracts frozen: {len(cases) + len(listing_cases)}")
