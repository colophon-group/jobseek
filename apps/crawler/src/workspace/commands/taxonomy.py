"""ws taxonomy — search and validate taxonomy CSV files."""

from __future__ import annotations

import re

import click
import polars as pl

from src.shared.constants import get_data_dir


def _detect_format(df: pl.DataFrame) -> str:
    """Detect CSV format: 'slug', 'id', or 'legacy'."""
    cols = set(df.columns)
    if "slug" in cols and "en" in cols:
        return "slug"
    if "id" in cols and "en" in cols:
        return "id"
    if "id" in cols and "name" in cols:
        return "legacy"
    raise click.ClickException(f"Unrecognized CSV format. Columns: {', '.join(df.columns)}")


def _load_taxonomy(name: str) -> tuple[pl.DataFrame, str]:
    """Load a taxonomy CSV by name and detect its format."""
    path = get_data_dir() / f"{name}.csv"
    if not path.exists():
        raise click.ClickException(f"Taxonomy file not found: {path}")
    df = pl.read_csv(path, infer_schema_length=0)
    fmt = _detect_format(df)
    return df, fmt


def _score_match(query: str, text: str, base_score: int) -> int:
    """Score a match: exact = base, substring = base - 40."""
    q = query.lower()
    t = text.lower()
    if q == t:
        return base_score
    if q in t or t in q:
        return base_score - 40
    return 0


def _search_localized(df: pl.DataFrame, query: str, key_col: str) -> list[tuple[int, str, str]]:
    """Search a localized taxonomy (with en, de, fr, it columns and optional aliases)."""
    results: list[tuple[int, str, str]] = []
    locales = [c for c in ["en", "de", "fr", "it"] if c in df.columns]

    for row in df.iter_rows(named=True):
        key = str(row[key_col])
        best_score = 0
        match_source = ""

        # Score key (slug or id)
        if key_col == "slug":
            s = _score_match(query, key.replace("-", " "), 100)
            if s > best_score:
                best_score = s
                match_source = f"slug: {key}"

        # Score display names
        for locale in locales:
            name = row.get(locale, "")
            if name:
                s = _score_match(query, name, 90)
                if s > best_score:
                    best_score = s
                    match_source = f"{locale}: {name}"

        # Score aliases
        aliases_raw = row.get("aliases", "")
        if aliases_raw:
            for alias in aliases_raw.split("|"):
                alias = alias.strip()
                if alias:
                    s = _score_match(query, alias, 80)
                    if s > best_score:
                        best_score = s
                        match_source = f"alias: {alias}"

        if best_score > 0:
            display_names = " | ".join(f"{lc}={row.get(lc, '')}" for lc in locales if row.get(lc))
            results.append((best_score, key, f"{display_names}  ({match_source})"))

    results.sort(key=lambda x: (-x[0], x[1]))
    return results


def _search_legacy(df: pl.DataFrame, query: str) -> list[tuple[int, str, str]]:
    """Search legacy taxonomy (id, name, keywords)."""
    results: list[tuple[int, str, str]] = []

    for row in df.iter_rows(named=True):
        row_id = row["id"]
        name = row["name"]
        best_score = 0
        match_source = ""

        s = _score_match(query, name, 90)
        if s > best_score:
            best_score = s
            match_source = f"name: {name}"

        keywords_raw = row.get("keywords", "")
        if keywords_raw:
            for kw in keywords_raw.split(","):
                kw = kw.strip()
                if kw:
                    s = _score_match(query, kw, 80)
                    if s > best_score:
                        best_score = s
                        match_source = f"keyword: {kw}"

        if best_score > 0:
            results.append((best_score, row_id, f"{name}  ({match_source})"))

    results.sort(key=lambda x: (-x[0], x[1]))
    return results


def _validate_localized(df: pl.DataFrame, key_col: str) -> list[str]:
    """Validate a localized taxonomy CSV (slug or id keyed)."""
    errors: list[str] = []
    locales = [c for c in ["en", "de", "fr", "it"] if c in df.columns]

    # Check for duplicate keys
    keys = df[key_col].to_list()
    seen: dict[str, int] = {}
    for i, key in enumerate(keys, 1):
        if key in seen:
            errors.append(f"Row {i}: duplicate {key_col} '{key}' (first at row {seen[key]})")
        else:
            seen[key] = i

    # Check slug format (only for slug-keyed)
    if key_col == "slug":
        slug_re = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
        for i, slug in enumerate(keys, 1):
            if not slug_re.match(slug):
                errors.append(f"Row {i}: invalid slug format '{slug}' (must be kebab-case)")

    # Check for missing translations
    for row in df.iter_rows(named=True):
        key = row[key_col]
        for locale in locales:
            val = row.get(locale, "")
            if not val or not val.strip():
                errors.append(f"'{key}': missing {locale} translation")

    # Check for ambiguous aliases (only if aliases column exists)
    if "aliases" in df.columns:
        alias_map: dict[str, list[str]] = {}
        for row in df.iter_rows(named=True):
            key = str(row[key_col])
            aliases_raw = row.get("aliases", "")
            if aliases_raw:
                for alias in aliases_raw.split("|"):
                    alias = alias.strip().lower()
                    if alias:
                        alias_map.setdefault(alias, []).append(key)

        for alias, key_list in alias_map.items():
            if len(key_list) > 1:
                errors.append(f"Ambiguous alias '{alias}' maps to: {', '.join(key_list)}")

    return errors


def _validate_legacy(df: pl.DataFrame) -> list[str]:
    """Validate legacy taxonomy CSV."""
    errors: list[str] = []

    ids = df["id"].to_list()
    seen_ids: dict[str, int] = {}
    for i, row_id in enumerate(ids, 1):
        if row_id in seen_ids:
            errors.append(f"Row {i}: duplicate id '{row_id}' (first at row {seen_ids[row_id]})")
        else:
            seen_ids[row_id] = i

    names = df["name"].to_list()
    for i, name in enumerate(names, 1):
        if not name or not name.strip():
            errors.append(f"Row {i}: empty name")

    return errors


@click.group()
def taxonomy_group():
    """Search and validate taxonomy CSV files."""


@taxonomy_group.command(name="search")
@click.argument("name")
@click.argument("query")
def taxonomy_search(name: str, query: str):
    """Fuzzy search a taxonomy CSV.

    NAME is the taxonomy file name (without .csv), e.g. 'occupations' or 'industries'.
    QUERY is the search term.
    """
    df, fmt = _load_taxonomy(name)

    if fmt == "slug":
        results = _search_localized(df, query, "slug")
    elif fmt == "id":
        results = _search_localized(df, query, "id")
    else:
        results = _search_legacy(df, query)

    if not results:
        print(f"No matches for '{query}' in {name}")
        return

    print(f"Top matches for '{query}' in {name}:\n")
    for score, key, detail in results[:20]:
        print(f"  [{score:>3}]  {key:<35}  {detail}")


@taxonomy_group.command(name="validate")
@click.argument("name")
def taxonomy_validate(name: str):
    """Validate a taxonomy CSV for errors.

    NAME is the taxonomy file name (without .csv), e.g. 'occupations' or 'industries'.
    """
    df, fmt = _load_taxonomy(name)

    if fmt == "slug":
        errors = _validate_localized(df, "slug")
    elif fmt == "id":
        errors = _validate_localized(df, "id")
    else:
        errors = _validate_legacy(df)

    if not errors:
        print(f"{name}: OK ({len(df)} entries, format={fmt})")
        return

    print(f"{name}: {len(errors)} error(s) found\n")
    for err in errors:
        print(f"  - {err}")
    raise SystemExit(1)


@taxonomy_group.command(name="add")
@click.argument("name")
@click.option("--en", required=True, help="English name")
@click.option("--de", required=True, help="German name")
@click.option("--fr", required=True, help="French name")
@click.option("--it", required=True, help="Italian name")
def taxonomy_add(name: str, en: str, de: str, fr: str, it: str):
    """Add a new entry to a taxonomy CSV.

    NAME is the taxonomy file name (without .csv), e.g. 'industries'.
    All four locale names are required.
    """
    from src.shared.constants import get_data_dir
    from src.shared.csv_io import read_csv, write_csv

    path = get_data_dir() / f"{name}.csv"
    if not path.exists():
        raise click.ClickException(f"Taxonomy file not found: {path}")

    headers, rows = read_csv(path)

    # Detect key column
    if "id" in headers:
        key_col = "id"
    elif "slug" in headers:
        raise click.ClickException(
            f"Taxonomy '{name}' uses slug keys. Only id-keyed taxonomies support auto-add."
        )
    else:
        raise click.ClickException(f"Unrecognized CSV format in {name}.csv")

    # Check for duplicates by en name
    for row in rows:
        if row.get("en", "").lower() == en.lower():
            raise click.ClickException(f"Entry with en={en!r} already exists (id={row[key_col]})")

    # Assign next ID
    max_id = max((int(r[key_col]) for r in rows if r.get(key_col, "").isdigit()), default=0)
    new_id = str(max_id + 1)

    new_row = {col: "" for col in headers}
    new_row[key_col] = new_id
    new_row["en"] = en
    new_row["de"] = de
    new_row["fr"] = fr
    new_row["it"] = it
    rows.append(new_row)

    write_csv(path, headers, rows)
    print(f"Added {name} id={new_id}: {en} / {de} / {fr} / {it}")
