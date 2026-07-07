from __future__ import annotations

import csv
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
LOCALE_RE = re.compile(r"^[a-z]{2}$")
HTTP_SCHEMES = {"http", "https"}

REQUIRED_COLUMNS = {
    "boards.csv": [
        "company_slug",
        "board_slug",
        "board_url",
        "monitor_type",
        "monitor_config",
        "scraper_type",
        "scraper_config",
    ],
    "companies.csv": [
        "slug",
        "name",
        "website",
        "logo_url",
        "icon_url",
        "logo_type",
        "industry",
        "employee_count_range",
        "founded_year",
        "extras",
    ],
    "company_descriptions.csv": ["slug", "en", "de", "fr", "it"],
    "industries.csv": ["id", "name", "keywords"],
    "occupation_domains.csv": ["slug", "en", "de", "fr", "it"],
    "occupations.csv": ["slug", "parent", "domain", "en", "de", "fr", "it", "aliases"],
    "seniority.csv": ["slug", "en", "de", "fr", "it", "aliases"],
    "technologies.csv": ["slug", "name", "category", "patterns", "flags"],
}

ALLOWED_MONITOR_TYPES = {
    "accenture",
    "almacareer",
    "amazon",
    "api_sniffer",
    "ashby",
    "bite",
    "breezy",
    "deel",
    "dom",
    "dvinci",
    "eightfold",
    "gem",
    "greenhouse",
    "hireology",
    "inline",
    "jobylon",
    "join",
    "lever",
    "mokahr",
    "nextdata",
    "notion",
    "oracle_hcm",
    "personio",
    "phenom",
    "pinpoint",
    "recruitee",
    "recruiter_co_kr",
    "rippling",
    "rss",
    "sitemap",
    "smartrecruiters",
    "softgarden",
    "talentbrew",
    "traffit",
    "umantis",
    "workable",
    "workday",
    "ycombinator",
}

ALLOWED_SCRAPER_TYPES = {
    "",
    "api_sniffer",
    "bite",
    "dom",
    "eightfold",
    "embedded",
    "json-ld",
    "mokahr",
    "nextdata",
    "notion",
    "oracle_hcm",
    "pdf",
    "rippling",
    "skip",
    "smartrecruiters",
    "workable",
    "workday",
}


class ValidationError(Exception):
    pass


def validate_header(name: str, fieldnames: Sequence[str] | None) -> None:
    expected = REQUIRED_COLUMNS[name]
    if name != "occupations.csv":
        if fieldnames != expected:
            raise ValidationError(f"{name}: expected header {expected!r}, got {fieldnames!r}")
        return

    if fieldnames is None:
        raise ValidationError(f"{name}: missing header")

    missing = [column for column in expected if column not in fieldnames]
    if missing:
        raise ValidationError(f"{name}: missing required header column(s) {missing!r}")

    if fieldnames[:3] != ["slug", "parent", "domain"] or fieldnames[-1:] != ["aliases"]:
        raise ValidationError(
            f"{name}: expected slug,parent,domain first and aliases last, got {fieldnames!r}"
        )

    for column in fieldnames[3:-1]:
        if not LOCALE_RE.fullmatch(column):
            raise ValidationError(
                f"{name}: unexpected non-locale column {column!r}; "
                "occupation display-name columns must be two-letter locale codes"
            )


def read_csv(name: str) -> list[dict[str, str]]:
    path = DATA / name
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        validate_header(name, reader.fieldnames)
        return list(reader)


def require_unique(rows: list[dict[str, str]], file_name: str, column: str) -> None:
    seen: dict[str, int] = {}
    for index, row in enumerate(rows, start=2):
        value = row[column].strip()
        if value in seen:
            first_line = seen[value]
            raise ValidationError(
                f"{file_name}:{index}: duplicate {column}={value!r}; "
                f"first seen on line {first_line}"
            )
        seen[value] = index


def require_slug(value: str, file_name: str, line: int, column: str) -> None:
    if not SLUG_RE.fullmatch(value):
        raise ValidationError(f"{file_name}:{line}: invalid {column} slug {value!r}")


def require_http_url(value: str, file_name: str, line: int, column: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in HTTP_SCHEMES or not parsed.netloc:
        raise ValidationError(f"{file_name}:{line}: invalid {column} URL {value!r}")


def require_optional_http_url(value: str, file_name: str, line: int, column: str) -> None:
    if value.strip():
        require_http_url(value, file_name, line, column)


def require_json_object(value: str, file_name: str, line: int, column: str) -> None:
    if not value.strip():
        return
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{file_name}:{line}: invalid {column} JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValidationError(f"{file_name}:{line}: {column} must be a JSON object")


def validate() -> None:
    rows = {name: read_csv(name) for name in REQUIRED_COLUMNS}

    for file_name, column in [
        ("companies.csv", "slug"),
        ("boards.csv", "board_slug"),
        ("boards.csv", "board_url"),
        ("industries.csv", "id"),
        ("occupation_domains.csv", "slug"),
        ("occupations.csv", "slug"),
        ("seniority.csv", "slug"),
        ("technologies.csv", "slug"),
    ]:
        require_unique(rows[file_name], file_name, column)

    company_slugs = {row["slug"] for row in rows["companies.csv"]}
    industry_ids = {row["id"] for row in rows["industries.csv"]}
    occupation_domains = {row["slug"] for row in rows["occupation_domains.csv"]}
    occupation_slugs = {row["slug"] for row in rows["occupations.csv"]}

    for index, row in enumerate(rows["companies.csv"], start=2):
        slug = row["slug"].strip()
        require_slug(slug, "companies.csv", index, "slug")
        if not row["name"].strip():
            raise ValidationError(f"companies.csv:{index}: name is required")
        require_http_url(row["website"], "companies.csv", index, "website")
        require_optional_http_url(row["logo_url"], "companies.csv", index, "logo_url")
        require_optional_http_url(row["icon_url"], "companies.csv", index, "icon_url")
        if row["industry"] and row["industry"] not in industry_ids:
            raise ValidationError(f"companies.csv:{index}: unknown industry id {row['industry']!r}")
        if row["founded_year"] and not row["founded_year"].isdigit():
            raise ValidationError(f"companies.csv:{index}: founded_year must be numeric")
        require_json_object(row["extras"], "companies.csv", index, "extras")

    for index, row in enumerate(rows["boards.csv"], start=2):
        require_slug(row["company_slug"], "boards.csv", index, "company_slug")
        require_slug(row["board_slug"], "boards.csv", index, "board_slug")
        if row["company_slug"] not in company_slugs:
            raise ValidationError(
                f"boards.csv:{index}: unknown company_slug {row['company_slug']!r}"
            )
        require_http_url(row["board_url"], "boards.csv", index, "board_url")
        if row["monitor_type"] not in ALLOWED_MONITOR_TYPES:
            raise ValidationError(
                f"boards.csv:{index}: unknown monitor_type {row['monitor_type']!r}"
            )
        if row["scraper_type"] not in ALLOWED_SCRAPER_TYPES:
            raise ValidationError(
                f"boards.csv:{index}: unknown scraper_type {row['scraper_type']!r}"
            )
        require_json_object(row["monitor_config"], "boards.csv", index, "monitor_config")
        require_json_object(row["scraper_config"], "boards.csv", index, "scraper_config")

    for index, row in enumerate(rows["company_descriptions.csv"], start=2):
        require_slug(row["slug"], "company_descriptions.csv", index, "slug")
        if row["slug"] not in company_slugs:
            raise ValidationError(
                f"company_descriptions.csv:{index}: unknown company slug {row['slug']!r}"
            )

    for index, row in enumerate(rows["industries.csv"], start=2):
        if not row["id"].isdigit():
            raise ValidationError(f"industries.csv:{index}: id must be numeric")
        if not row["name"].strip():
            raise ValidationError(f"industries.csv:{index}: name is required")

    for index, row in enumerate(rows["occupation_domains.csv"], start=2):
        require_slug(row["slug"], "occupation_domains.csv", index, "slug")

    for index, row in enumerate(rows["occupations.csv"], start=2):
        require_slug(row["slug"], "occupations.csv", index, "slug")
        if row["parent"] and row["parent"] not in occupation_slugs:
            raise ValidationError(f"occupations.csv:{index}: unknown parent {row['parent']!r}")
        if row["domain"] not in occupation_domains:
            raise ValidationError(f"occupations.csv:{index}: unknown domain {row['domain']!r}")

    for index, row in enumerate(rows["seniority.csv"], start=2):
        require_slug(row["slug"], "seniority.csv", index, "slug")

    for index, row in enumerate(rows["technologies.csv"], start=2):
        require_slug(row["slug"], "technologies.csv", index, "slug")
        if not row["name"].strip():
            raise ValidationError(f"technologies.csv:{index}: name is required")


def main() -> int:
    try:
        validate()
    except ValidationError as exc:
        print(f"data validation failed: {exc}", file=sys.stderr)
        return 1
    print("data validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
