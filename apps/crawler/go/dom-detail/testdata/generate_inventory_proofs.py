"""Freeze configured DOM/API inventory proofs through production Python."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.monitors import api_sniffer, dom

api_cases = []
for name, payload, config in [
    ("false", {"found_jobs": False}, {"found_jobs": False}),
    ("boolean-not-integer", {"found_jobs": 0}, {"found_jobs": False}),
    ("integer", {"total": 0}, {"total": 0}),
    ("float-not-integer", {"total": 0.0}, {"total": 0}),
    ("float", {"total": 0.0}, {"total": 0.0}),
    ("int-not-float", {"total": 0}, {"total": 0.0}),
    ("array", {"jobs": []}, {"jobs": []}),
    ("nonempty-array", {"jobs": [1]}, {"jobs": []}),
    ("null-not-array", {"jobs": None}, {"jobs": []}),
    ("missing-null", {}, {"missing": None}),
    ("nested", {"result": {"jobs": []}}, {"result.jobs": []}),
    ("multiple", {"jobs": [], "total": 0}, {"jobs": [], "total": 0}),
    ("multiple-failure", {"jobs": [], "total": 1}, {"jobs": [], "total": 0}),
    ("string", {"status": "no-jobs"}, {"status": "no-jobs"}),
    ("invalid-object-marker", {}, {"status": {}}),
    ("invalid-list-marker", {}, {"status": [1]}),
    ("invalid-empty-config", {}, {}),
]:
    try:
        matches = api_sniffer._matches_explicit_empty_response(payload, config)
        error = False
    except Exception:
        matches, error = None, True
    api_cases.append(dict(name=name, payload=payload, config=config, matches=matches, error=error))

board = "https://example.com/careers"
dom_cases = []


def add(name, body, config, count):
    urls = {f"https://example.com/jobs/{i}" for i in range(count)}
    try:
        total = dom._validated_advertised_total_config(config.get("advertised_total"))
        if total:
            dom._validate_advertised_total(body, total, urls, board)
        states = dom._validated_empty_state_list(config.get("empty_states"))
        if states:
            dom._validate_explicit_empty_states(body, states, urls, board)
        error = False
    except Exception:
        error = True
    dom_cases.append(dict(name=name, body=body, config=config, count=count, error=error))


total = dict(advertised_total=dict(selector=".total", regex=r"(\d+) jobs"))
for name, body, count in [
    ("total", '<div class="total">2 jobs</div>', 2),
    ("zero", '<div class="total">0 jobs</div>', 0),
    ("unicode", '<div class="total">٢ jobs</div>', 2),
    ("fullwidth", '<div class="total">２ jobs</div>', 2),
    ("mismatch", '<div class="total">2 jobs</div>', 1),
    ("missing", "<html/>", 0),
    ("conflict", '<div class="total">2 jobs</div><div class="total">3 jobs</div>', 2),
    ("matching-multiple", '<div class="total">2 jobs</div><div class="total">2 jobs</div>', 2),
    ("no-partial-match", '<div class="total">2 jobs extra</div>', 2),
]:
    add(name, body, total, count)
add(
    "alternation-backtracking",
    '<div class="total">01</div>',
    dict(advertised_total=dict(selector=".total", regex=r"(0|01)")),
    1,
)
for name, regex in [
    ("no-capture", r"\d+ jobs"),
    ("two-captures", r"(\d+) (jobs)"),
    ("bad-regex", "("),
]:
    add(
        name,
        '<div class="total">2 jobs</div>',
        dict(advertised_total=dict(selector=".total", regex=regex)),
        2,
    )

for name, body, state, count in [
    (
        "exact",
        '<div class="empty">No\n jobs</div>',
        dict(selector=".empty", exact_text="No jobs"),
        0,
    ),
    (
        "exact-case",
        '<div class="empty">NO JOBS</div>',
        dict(selector=".empty", exact_text="No jobs"),
        0,
    ),
    (
        "contains-fold",
        '<div class="empty">Straße is closed</div>',
        dict(selector=".empty", contains_text="STRASSE"),
        0,
    ),
    (
        "first-only",
        '<div class="empty">Other</div><div class="empty">No jobs</div>',
        dict(selector=".empty", contains_text="No jobs"),
        0,
    ),
    (
        "exact-later",
        '<div class="empty">Other</div><div class="empty">No jobs</div>',
        dict(selector=".empty", exact_text="No jobs"),
        0,
    ),
    ("positive-no-empty-proof", "<html/>", dict(selector=".empty", exact_text="No jobs"), 1),
    (
        "contradiction",
        '<div class="empty">No jobs</div><a class="job">Job</a>',
        dict(selector=".empty", exact_text="No jobs", forbidden_link_selector="a.job"),
        1,
    ),
    (
        "required-link",
        '<div class="empty">No jobs</div><a class="general" href="/apply">Apply</a>',
        dict(
            selector=".empty",
            exact_text="No jobs",
            required_link_selector="a.general",
            required_link_url_pattern=r"https://example\.com/apply",
        ),
        0,
    ),
    (
        "missing-required-link",
        '<div class="empty">No jobs</div>',
        dict(
            selector=".empty",
            exact_text="No jobs",
            required_link_selector="a.general",
            required_link_url_pattern=r"https://example\.com/apply",
        ),
        0,
    ),
    (
        "foreign-link",
        '<div class="empty">No jobs</div><a class="general" href="https://foreign.example/apply">Apply</a>',
        dict(
            selector=".empty",
            exact_text="No jobs",
            required_link_selector="a.general",
            required_link_url_pattern=r"https://example\.com/apply",
        ),
        0,
    ),
    (
        "unpaired-link",
        "<html/>",
        dict(selector=".empty", exact_text="No jobs", required_link_selector="a.general"),
        0,
    ),
    (
        "two-text-keys",
        "<html/>",
        dict(selector=".empty", exact_text="No jobs", contains_text="No jobs"),
        0,
    ),
]:
    add(name, body, dict(empty_states=[state]), count)

Path(__file__).with_name("python_inventory_proofs.json").write_text(
    json.dumps(dict(api_cases=api_cases, dom_cases=dom_cases), ensure_ascii=False, indent=2) + "\n"
)
print(f"froze {len(api_cases)} API and {len(dom_cases)} DOM proof cases")
