"""Freeze full-taxonomy multilingual outputs from the retained Python matchers."""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from src.core.enum_normalize import _INTERNSHIP_LEVEL_SIGNALS
from src.core.occupation_resolve import _load_token_aliases, match_occupation
from src.core.seniority_resolve import match_seniority
from src.core.technology_resolve import match_technologies

root = Path(__file__).resolve().parents[3]
rows = list(csv.DictReader((root / "data/occupations.csv").open()))
titles = []
for row in rows:
    titles.append(row["slug"].replace("-", " "))
    titles.extend(
        v for k, v in row.items() if k not in {"slug", "parent", "domain", "aliases"} and v
    )
    titles.extend(x.strip() for x in row["aliases"].split("|") if x.strip())
titles = list(dict.fromkeys(titles))
rng = random.Random(42)
for raw in rng.sample(titles, min(len(titles), 120)):
    titles.extend(["Senior " + raw + " (m/f/d)", "(" + raw + "): role", "X" + raw + "X"])
for tokens, _, _ in _load_token_aliases():
    titles.append("lead " + " consulting ".join(sorted(tokens, reverse=True)))
titles.extend(
    [
        "Lead Generation Manager",
        "Lead Qualification Analyst",
        "Lead Developer",
        "Team Lead漢",
        "CEO Advisory",
        "CEO Advisor",
        "CTO",
        "CTO漢",
        "Art Director",
        "Director of Art",
        "Creative Director",
        "Funeral Director",
        "Vice President Sales",
        "Geschäftsführer",
        "Senior Internship",
        "Google Stage 2",
        "Stage ٢ Internship",
        "Working Student",
        "Graduate Program",
        "New Grad",
        "new gradé",
        "Internship漢",
        "ΠΡΟΓΡΑΜΜΑΤΙΣΤΉΣ",
        "Technicien(ne)",
        "Technicien/ne",
        "Supervisor/euse",
        "(h/e) Software Engineer",
        "",
    ]
)
occupation = [
    {"text": t, "occupation": match_occupation(t), "seniority": match_seniority(t)} for t in titles
]
tech_texts = [
    "",
    "Python and React <b>SQL</b>",
    "<script>Python &amp; Java</script> R",
    "Py<!-- comment -->thon",
    "<p",
    "Java <script",
    "Python &notanentity;",
    "&#80;ython",
    "<p>Python</p>&#82;",
    "λPython λGo Goλ .NETx x.NET C++x",
]
for row in csv.DictReader((root / "data/technologies.csv").open()):
    for alt in row["patterns"].split("|"):
        alt = alt.strip()
        if alt:
            tech_texts.extend(
                [
                    alt,
                    alt.lower(),
                    alt.upper(),
                    "x" + alt + "x",
                    "(" + alt + ")",
                    "汉" + alt + "汉",
                    "<b>" + alt + "</b>",
                ]
            )
tech_texts = list(dict.fromkeys(tech_texts))
fixtures = {
    "titles": occupation,
    "technologies": [{"text": t, "slugs": match_technologies(t)} for t in tech_texts],
    "intern_signals": sorted(_INTERNSHIP_LEVEL_SIGNALS),
}
Path(__file__).with_name("python_taxonomy.json").write_text(
    json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n"
)
print(len(occupation), "title cases;", len(tech_texts), "technology cases")
