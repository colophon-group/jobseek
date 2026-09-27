from __future__ import annotations

import ast
import json
from pathlib import Path

from src.core import experience_extract as e

root = Path("apps/crawler/go/job-enrichment")
names = [
    "_EXPERIENCE_MIXED_RANGE_RE",
    "_EXPERIENCE_RE",
    "_EXPERIENCE_REVERSED_RE",
    "_FALSE_POSITIVE_RE",
    "_MONTH_UNIT_RE",
]
patterns = {n: (v.pattern if hasattr(v, "pattern") else v) for n in names if (v := getattr(e, n))}
(root / "experience_patterns.json").write_text(
    json.dumps(patterns, ensure_ascii=False, indent=2) + "\n"
)
cases = []
for n in ast.walk(ast.parse(Path("apps/crawler/tests/test_experience_extract.py").read_text())):
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        cases.append(n.value)
for n in ["0", "0.5", "1", "1.5", "2,5", "5", "30", "31", "99", "100", "１２", "١٢"]:
    for unit in [
        "years",
        "months",
        "Jahre",
        "Monate",
        "ans",
        "mois",
        "anni",
        "mesi",
        "años",
        "meses",
        "jaar",
        "maanden",
        "år",
        "månader",
        "måneder",
    ]:
        for qualifier in [
            "experience",
            "of relevant experience",
            "de experiencia",
            "software engineering experience",
            "of unrelated historical information",
        ]:
            cases.extend([f"{n}+ {unit} {qualifier}", f"{n}-5 {unit} {qualifier}"])
cases.extend(
    [
        "6 months to 1 year work experience",
        "6 months to 1 year work experience; 1 year experience",
        "founded 5 years experience",
        "Intern: 6 months experıence",
        "3 years of experİence",
        "7 months to 1.5 years of experience",
        "5 years experience " + "a" * 60 + " 2 years experience",
    ]
)
# Preserve the full optional-prefix start when checking the preceding
# 60 Unicode characters, and preserve non-overlapping matches in each pass.
for prefix in [
    "",
    "at least ",
    "minimum ",
    "au moins ",
    "MINİMUM ",
    "minimo ",
    "mindestens ",
    "minimum at least ",
    "minimum minimum ",
]:
    for distance in [0, 48, 52, 55, 60, 61]:
        for body in ["5 years experience", "6 months to 1 year work experience"]:
            cases.append("founded " + "é" * distance + " " + prefix + body)
            cases.append(prefix + body + "; " + prefix + "2 years experience")
rows = []
for text in dict.fromkeys(cases):
    result = e.extract_experience(text)
    rows.append(
        {
            "text": text,
            "min": result.min_years if result else None,
            "max": result.max_years if result else None,
        }
    )
(root / "testdata/python_experience.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
)
print(len(rows))
