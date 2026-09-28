"""Freeze the complete legacy salary contract; no publisher requests."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from src.core import salary_extract as s

root = Path("apps/crawler/go/job-enrichment")
source = Path("apps/crawler/src/core/salary_extract.py")
tree = ast.parse(source.read_text())
patterns = {
    name: {"pattern": value.pattern, "ignore_case": bool(value.flags & re.I)}
    for name, value in vars(s).items()
    if isinstance(value, re.Pattern)
}
for code, value in s._EU_RES.items():
    patterns[code] = {"pattern": value.pattern, "ignore_case": True}
inline = {}
tokens = {}
for fn in tree.body:
    if not isinstance(fn, ast.FunctionDef):
        continue
    for n in ast.walk(fn):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "re"
            and isinstance(n.args[0], ast.Constant)
        ):
            key = fn.name + ":" + n.func.attr
            assert key not in inline
            inline[key] = {
                "pattern": n.args[0].value,
                "ignore_case": any(
                    isinstance(a, ast.Attribute) and a.attr == "IGNORECASE" for a in n.args[1:]
                )
                or any(
                    isinstance(k.value, ast.Attribute) and k.value.attr == "IGNORECASE"
                    for k in n.keywords
                ),
            }
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Tuple)
            and all(isinstance(e, ast.Constant) for e in n.value.elts)
        ):
            for target in n.targets:
                if isinstance(target, ast.Name) and target.id.endswith("_tokens"):
                    tokens[target.id] = ast.literal_eval(n.value)
patterns.update(inline)
# re.fullmatch is an anchored whole-string operation.
patterns["_is_european_decimal:fullmatch"]["pattern"] = (
    r"\A(?:" + patterns["_is_european_decimal:fullmatch"]["pattern"] + r")\z"
)
(root / "salary_rules.json").write_text(
    json.dumps(
        {
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "patterns": patterns,
            "mojibake": s._MOJIBAKE_TABLE,
            "periods": list(s._PERIOD_MAP.items()),
            "prefixes": s._PREFIX_TO_CURRENCY,
            "currencies": [{"code": code, **v} for code, v in s._EU_CURRENCIES.items()],
            "tokens": tokens,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
texts = []
for name in [
    "test_salary_extract.py",
    "test_salary_extract_eu.py",
    "test_salary_extract_eu_locale.py",
    "test_cpu_salary_annualization.py",
]:
    for n in ast.walk(ast.parse((Path("apps/crawler/tests") / name).read_text())):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            texts.append(n.value)
for prefix in ["", "A", "AU", "NZ", "S", "HK", "R", "MX", "US", "CDN", "C", "CA"]:
    for number in [
        "5",
        "25.51",
        "499.99",
        "500",
        "10000",
        "15000",
        "80K",
        "120,000",
        "50.000,00",
        "١٢٠٠٠٠",
        "１２５０００",
    ]:
        for period in [" per year", "/hr", " per month", " + bonus + equity annually"]:
            texts.append(f"<p>Salary: {prefix}${number}{period}</p>")
for _code, spec in s._EU_CURRENCIES.items():
    for symbol in spec["symbols"]:
        symbol = symbol.replace("\\.?", ".")
        for period, native in [
            ("hourly", "/hour"),
            ("monthly", " per month"),
            ("yearly", " per year"),
        ]:
            lo = spec[
                {"hourly": "hourly_min", "monthly": "monthly_min", "yearly": "range_min"}[period]
            ]
            for amount in [
                lo - 1,
                lo,
                lo + 1,
                spec[
                    {"hourly": "hourly_max", "monthly": "monthly_max", "yearly": "range_max"}[
                        period
                    ]
                ],
            ]:
                for context in [
                    "Salary gross",
                    "Salary net",
                    "Salary gross net",
                    "Salary voucher",
                    "Salary revenue",
                ]:
                    texts.append(f"{context}: {symbol} {amount}{native}")
                    texts.append(f"{context}: {amount} - {amount + 1} {symbol}{native}")
for char in [
    "\u001c",
    "\u0085",
    "\u00a0",
    "\u202f",
    "İ",
    "ı",
    "ſ",
    "K",
    "\u0301",
    "²",
    "é",
    "_",
    "\n",
]:
    for body in [
        "Salary: €50,000 - €60,000 per year",
        "$25.51/hr",
        "salary £25 to £30 an hour",
        "Salary CHF 25 - 30 pro Stunde",
        "salary PLN 2000 per month",
        "USA, NV, Sparks - 25.01 - 30.00 USD hourly",
    ]:
        texts.extend([char + body + char, body.replace(" ", char)])
for distance in [99, 100, 101, 149, 150, 151, 199, 200, 201]:
    for body in ["€50000", "£50000-£60000", "CHF 120000", "PLN 50000", "A$50000"]:
        texts.extend(
            ["Salary " + "é" * distance + " " + body, body + " " + "é" * distance + " salary"]
        )
texts.extend(
    [
        "salary €50000; salary $50000/year",
        "salary $50000/year; €60000 salary",
        "salary £50000/year; salary €50000/year",
        "Salary: Â£25.00 â€“ Â£30.00 an hour",
        "Salary: R$50.000",
        "salary $25.51/hr; $25.51/hr",
        "Salary $15000 - $14000 annually",
        "Salary CHF 120000 - 0 per year",
        "Salary CHF 120000 - 100000 per year",
        "",
    ]
)
# Large representable raw integers, unbounded annualization intermediates,
# double rounding and mixed currency/period tie order.
texts.extend(
    [
        "US, CA, City - 10000000000000000 - 10000000000000002 USD annually",
        "US, CA, City - 10000000000000000 - 10000000000000002 USD hourly",
        "$900000000000000000/year",
        "12000 - 14000 EUR monthly",
        "Salary €50000 - €60000; Salary $50000/year",
        "US, CA, City - 25.51 - 30.99 USD hourly",
        "Salary $25.51/hr; $25.52/hr",
    ]
)
rows = []
for i, text in enumerate(dict.fromkeys(texts)):
    rates = [
        {},
        {c: 1.0 for c in ["USD", "CAD", "EUR", "GBP", "CHF", *s._EU_CURRENCIES]},
        {
            "USD": 0.9,
            "EUR": 1.0,
            "GBP": 1.17,
            "CHF": 1.05,
            "PLN": 0.23,
            "CZK": 0.04,
            "SEK": 0.087,
            "DKK": 0.134,
            "HUF": 0.0025,
            "RON": 0.2,
            "BGN": 0.51,
            "AUD": 0.6,
            "BRL": 0.17,
        },
        {"USD": 0.0, "EUR": -1.0},
        {c: 0.0001 for c in ["USD", "EUR", "GBP", "CHF", *s._EU_CURRENCIES]},
    ][i % 5]
    ranges = s._extract_salary_python(text)
    unified = s._extract_salary_unified_python(text)
    eur = None
    if unified:
        annual = (
            round(unified.min / 100 * 2080)
            if unified.period == "hourly"
            else unified.min * 12
            if unified.period == "monthly"
            else unified.min
        )
        rate = rates.get(unified.currency, 0)
        eur = round(annual * rate) if rate > 0 else None
    rows.append(
        {
            "text": text,
            "rates": rates,
            "ranges": [asdict(r) for r in ranges],
            "unified": asdict(unified) if unified else None,
            "parsed": s._parse_salary_text_python(text),
            "eur": eur,
        }
    )
(root / "testdata/python_salary.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
)
print(
    json.dumps(
        {
            "cases": len(rows),
            "positive": sum(bool(r["ranges"]) for r in rows),
            "currencies": sorted({v["currency"] for r in rows for v in r["ranges"]}),
        }
    )
)
