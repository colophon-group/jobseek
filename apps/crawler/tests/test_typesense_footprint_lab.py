"""Pure regression tests for the disposable Typesense footprint lab."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/typesense-footprint-lab.py"
SPEC = importlib.util.spec_from_file_location("typesense_footprint_lab", SCRIPT)
assert SPEC and SPEC.loader
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)


def _fields(schema: dict) -> dict[str, dict]:
    return {field["name"]: field for field in schema["fields"]}


def test_baseline_preserves_canonical_schema() -> None:
    canonical = lab.load_job_posting_schema(REPO_ROOT)
    baseline = lab.build_variant_schema(canonical, "baseline")

    assert baseline["name"] == lab.LAB_COLLECTION
    assert baseline["fields"] == canonical["fields"]
    assert baseline["default_sorting_field"] == canonical["default_sorting_field"]


def test_response_variant_stores_but_does_not_index_response_only_fields() -> None:
    canonical = lab.load_job_posting_schema(REPO_ROOT)
    candidate = lab.build_variant_schema(canonical, "response-unindexed")
    canonical_fields = _fields(canonical)
    candidate_fields = _fields(candidate)

    assert candidate_fields.keys() == canonical_fields.keys()
    for name in lab.RESPONSE_ONLY_INDEXED_FIELDS:
        assert candidate_fields[name]["index"] is False
        assert candidate_fields[name]["facet"] is False
        assert candidate_fields[name]["sort"] is False

    for name in candidate_fields.keys() - lab.RESPONSE_ONLY_INDEXED_FIELDS:
        assert candidate_fields[name] == canonical_fields[name]


def test_response_variant_can_isolate_one_field() -> None:
    candidate = lab.build_variant_schema(
        lab.load_job_posting_schema(REPO_ROOT),
        "response-unindexed",
        response_fields=frozenset({"company_name"}),
    )
    fields = _fields(candidate)

    assert fields["company_name"]["index"] is False
    assert fields["location_names"].get("index", True) is True


def test_pre_tuning_baseline_restores_selected_index_and_facet_shapes() -> None:
    restored = lab.restore_pre_tuning_indexes(
        lab.load_job_posting_schema(REPO_ROOT),
        frozenset({"occupation_id", "occupation_name", "last_seen_at"}),
    )
    fields = _fields(restored)

    assert fields["occupation_id"].get("index", True) is True
    assert fields["occupation_id"]["facet"] is True
    assert fields["occupation_name"].get("index", True) is True
    assert fields["occupation_name"]["facet"] is True
    assert fields["last_seen_at"].get("index", True) is True
    assert "facet" not in fields["last_seen_at"]
    assert "sort" not in fields["last_seen_at"]


def test_default_benchmark_targets_only_fields_still_indexed_canonically() -> None:
    args = lab.build_parser().parse_args(["benchmark", "sample.jsonl.gz"])

    assert set(args.response_fields) == (
        lab.RESPONSE_ONLY_INDEXED_FIELDS - lab.INITIAL_PRODUCTION_TUNING_FIELDS
    )
    assert args.baseline_indexed_fields == []


def test_combined_variant_retains_observed_facet_and_sort_contracts() -> None:
    candidate = lab.build_variant_schema(
        lab.load_job_posting_schema(REPO_ROOT),
        "combined-pruned",
    )
    fields = _fields(candidate)

    assert {name for name, field in fields.items() if field["facet"]} == set(
        lab.REQUIRED_FACET_FIELDS
    )
    assert {name for name, field in fields.items() if field["sort"]} == set(
        lab.REQUIRED_SORT_FIELDS
    )
    assert fields["salary_eur"]["sort"] is True
    assert fields["company_id"]["facet"] is True


def test_parity_projection_ignores_unconsumed_numeric_facet_stats() -> None:
    result = {
        "found": 2,
        "hits": [{"document": {"id": "one"}}, {"document": {"id": "two"}}],
        "facet_counts": [
            {
                "field_name": "experience_min",
                "counts": [{"value": "3", "count": 2}],
                "stats": {"avg": 3.0, "sum": 6.0},
            }
        ],
    }

    projection = lab._result_projection(result)

    assert projection["facets"] == [
        {
            "field_name": "experience_min",
            "counts": [{"value": "3", "count": 2}],
            "total_values": None,
        }
    ]


def test_keyword_projection_keeps_company_total_but_ignores_tied_value() -> None:
    result = {
        "found": 2,
        "facet_counts": [
            {
                "field_name": "company_id",
                "counts": [{"value": "arbitrary-tied-company", "count": 1}],
                "stats": {"total_values": 2},
            }
        ],
    }

    projection = lab._consumed_result_projection("keyword_grouped", result)

    assert projection["facets"] == [{"field_name": "company_id", "total_values": 2}]


def test_percentile_uses_nearest_rank() -> None:
    assert lab._percentile([1, 2, 3, 4, 5], 0.95) == 5
    assert lab._percentile([5, 1, 4, 2, 3], 0.50) == 3


def test_checked_in_consumer_matrix_covers_every_posting_field() -> None:
    document = (REPO_ROOT / "docs/typesense-footprint-investigation-2026-08-26.md").read_text()
    matrix = document.split("## Field-consumer matrix", 1)[1].split(
        "### Read-path coverage",
        1,
    )[0]
    documented = set(re.findall(r"^\| `([^`]+)`(?: \(implicit\))? \|", matrix, re.MULTILINE))
    canonical = lab.load_job_posting_schema(REPO_ROOT)
    expected = {"id", *(field["name"] for field in canonical["fields"])}

    assert documented == expected
