from __future__ import annotations

import copy
import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

V1 = Path(__file__).resolve().parents[2]
MANIFEST_PATH = V1 / "fixtures" / "semantics" / "manifest.json"
CHECKER_PATH = V1 / "tools" / "check_semantics.py"

_SPEC = importlib.util.spec_from_file_location("runtime_v1_semantics", CHECKER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
semantics: Any = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = semantics
_SPEC.loader.exec_module(semantics)

REQUIRED_CASE_IDS = frozenset(
    {
        "absent_vs_empty",
        "canonical_collision",
        "digest_sensitivity",
        "divergent_rich_collision",
        "browser_suppressed",
        "invalid_html_nesting_limit",
        "invalid_html_unclosed_comment",
        "invalid_html_unclosed_quote",
        "invalid_html_unclosed_suppressed",
        "invalid_projection_alignment",
        "invalid_projection_malformed_url",
        "invalid_precondition_type",
        "invalid_shape_missing",
        "invalid_shape_unknown",
        "invalid_url",
        "invalid_url_above_root",
        "invalid_url_fragment_escape",
        "invalid_url_legacy_ip",
        "invalid_url_legacy_mixed_components",
        "invalid_url_non_ascii_host",
        "invalid_url_port_zero",
        "invalid_visible_content",
        "invalid_localized_description",
        "invalid_localized_description_null",
        "invalid_metadata_null",
        "invalid_unicode_surrogate",
        "language_alias",
        "language_rejection",
        "locale_alias",
        "locale_collision",
        "locale_rejection",
        "localized_only_visible",
        "monitor_batches_counter_overflow",
        "monitor_batches_ordered",
        "monitor_batches_sitemap_conflict",
        "monitor_incomplete",
        "ordered_metadata",
        "privacy_rejected",
        "rich_monitor",
        "rich_url_union_lockstep",
        "safe_monitor_url_only",
        "safe_scrape_projected",
        "safe_url_default_repeated_slash",
        "safe_url_query_distinctions",
        "safe_url_leading_zero_default_port",
        "safe_url_numeric_overrange_dns",
        "set_permutation_dedupe",
        "source_identity_collision_rejected",
        "source_identity_explicit_null_legacy_equivalence",
        "source_identity_invalid_present_rejected",
        "source_identity_valid_present_propagation_and_semantic_sensitivity",
        "suppressed_precondition",
        "unknown_subject_rejected",
        "visible_unterminated_space_entities",
    }
)


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_bytes())


def _case(case_id: str) -> dict[str, Any]:
    return next(case for case in _manifest()["cases"] if case["id"] == case_id)


def test_required_ids_are_independently_hard_coded_and_complete() -> None:
    manifest = _manifest()
    ids = [case["id"] for case in manifest["cases"]]
    assert frozenset(semantics.CASE_IDS) == REQUIRED_CASE_IDS
    assert manifest["required_case_ids"] == list(semantics.CASE_IDS)
    assert ids == list(semantics.CASE_IDS)
    assert len(ids) == len(set(ids)) == 54
    assert all(
        set(case) == {"expected", "id", "input", "subject_kind"} for case in manifest["cases"]
    )


@pytest.mark.parametrize("case_id", sorted(REQUIRED_CASE_IDS))
def test_every_corpus_case_matches_the_offline_projector(case_id: str) -> None:
    fixture = _case(case_id)
    assert semantics.project_case(fixture) == fixture["expected"]


def test_whole_manifest_validates_with_exact_results() -> None:
    manifest = _manifest()
    assert semantics.validate_manifest(manifest) == manifest["cases"]


def test_projected_digests_and_target_lists_are_exactly_aligned() -> None:
    for fixture in _manifest()["cases"]:
        expected = fixture["expected"]
        if expected["status"] != "projected":
            continue
        without_digest = {key: value for key, value in expected.items() if key != "semantic_sha256"}
        assert expected["semantic_sha256"] == semantics.semantic_sha256(without_digest)
        assert len(expected["semantic_sha256"]) == 64
        effects = expected["projected_effects"]
        length = len(effects["targets"])
        assert len(effects["urls_to_upsert"]) == length
        assert len(effects["content_hashes"]) == length
        assert len(effects["job_effects"]) == length
        for index, target in enumerate(effects["targets"]):
            assert effects["urls_to_upsert"][index] == target["url"]
            assert effects["job_effects"][index]["source_url"] == target["url"]
            digest = target.get("content_sha256", "")
            assert effects["content_hashes"][index] == digest
            assert effects["job_effects"][index]["content_sha256"] == digest


@pytest.mark.parametrize(
    ("html", "visible"),
    [
        ("<p>Synthetic role</p>", True),
        ("&copy;", True),
        ("&nbsp;&#160;&#xA0;\u200b", False),
        ("&nbsp;", False),
        ("&#160;", False),
        ("&#xA0;", False),
        ("&nbsp", True),
        ("&#160", True),
        ("&#xA0", True),
        ("<script>visible-looking</script>", False),
        ("<style>visible-looking</style>", False),
        ("<template>visible-looking</template>", False),
        ("<noscript>visible-looking</noscript>", False),
        ("<p hidden>visible-looking</p>", False),
        ("<p aria-hidden=TRUE>visible-looking</p>", False),
        ("<p style='DISPLAY: none'>visible-looking</p>", False),
        ("<p style='visibility: hidden'>visible-looking</p>", False),
        ("<p style=visibility:collapse>visible-looking</p>", False),
    ],
)
def test_visibility_tokenizer_observes_hidden_subtrees_and_closed_spaces(
    html: str, visible: bool
) -> None:
    assert semantics.has_visible_content(html) is visible


@pytest.mark.parametrize(
    "html",
    [
        "<section hidden>unclosed",
        "<script>unclosed",
        "<p>Visible</p><!-- unclosed",
        "<p hidden='unterminated>visible",
        "visible\u0000control",
        "<div>" * 129 + "visible" + "</div>" * 129,
        "x" * (1_048_576 + 1),
    ],
)
def test_visibility_tokenizer_rejects_unsafe_state(html: str) -> None:
    with pytest.raises(semantics.SemanticFailure) as caught:
        semantics.has_visible_content(html)
    assert caught.value.reason == "invalid_visible_content"


def test_url_profile_canonicalizes_the_seed_rules() -> None:
    assert (
        semantics.canonical_url("HTTPS://JOBS.EXAMPLE.INVALID:443/a/./b?z=2&a=&a#fragment")
        == "https://jobs.example.invalid/a/b?a&a=&z=2"
    )
    assert semantics.canonical_url("https://jobs.example.invalid/%7erole?q=a+b") == (
        "https://jobs.example.invalid/~role?q=a+b"
    )
    assert semantics.canonical_url("https://jobs.example.invalid") == (
        "https://jobs.example.invalid/"
    )
    assert semantics.canonical_url("https://jobs.example.invalid//a///b") == (
        "https://jobs.example.invalid//a///b"
    )
    assert semantics.canonical_url("HTTPS://jobs.example.invalid:0443/role") == (
        "https://jobs.example.invalid/role"
    )
    assert semantics.canonical_url("HTTP://jobs.example.invalid:080/role") == (
        "http://jobs.example.invalid/role"
    )
    assert semantics.canonical_url("https://4294967296/role") == ("https://4294967296/role")
    assert semantics.canonical_url("https://256.0.0.1/role") == ("https://256.0.0.1/role")


def test_consistency_repair_cases_have_closed_outcomes() -> None:
    malformed_projection = _case("invalid_projection_malformed_url")["expected"]
    assert malformed_projection == {
        "case_id": "invalid_projection_malformed_url",
        "reason": "invalid_projection",
        "status": "rejected",
    }
    invalid_localization = _case("invalid_localized_description")["expected"]
    assert invalid_localization == {
        "case_id": "invalid_localized_description",
        "reason": "invalid_visible_content",
        "status": "suppressed",
    }


def test_source_identity_amendment_is_legacy_stable_and_semantically_sensitive() -> None:
    explicit_null = _case("source_identity_explicit_null_legacy_equivalence")
    assert explicit_null["expected"]["status"] == "projected"
    null_effects = explicit_null["expected"]["projected_effects"]
    assert "source_identity" not in null_effects["job_effects"][0]

    omitted = copy.deepcopy(explicit_null)
    del omitted["input"]["result"]["jobs"][0]["source_identity"]
    del omitted["input"]["existing_projected_effects"]["job_effects"][0]["source_identity"]
    assert semantics.project_case(omitted) == explicit_null["expected"]

    present = _case("source_identity_valid_present_propagation_and_semantic_sensitivity")
    present_result = present["expected"]
    assert present_result["status"] == "projected"
    present_effects = present_result["projected_effects"]
    assert present_effects["job_effects"][0]["source_identity"] == ("smartrecruiters:example:42")
    assert present_effects["content_hashes"] == null_effects["content_hashes"]

    changed = copy.deepcopy(present)
    changed["input"]["result"]["jobs"][0]["source_identity"] = "smartrecruiters:example:43"
    changed_result = semantics.project_case(changed)
    assert changed_result["status"] == "projected"
    assert (
        changed_result["projected_effects"]["content_hashes"] == present_effects["content_hashes"]
    )
    assert changed_result["semantic_sha256"] != present_result["semantic_sha256"]

    for case_id in ("safe_scrape_projected", "safe_monitor_url_only"):
        for effect in _case(case_id)["expected"]["projected_effects"]["job_effects"]:
            assert "source_identity" not in effect


@pytest.mark.parametrize("invalid", [42, "", "not-namespaced", "SmartRecruiters:example:42"])
def test_source_identity_invalid_type_or_pattern_rejects(invalid: object) -> None:
    fixture = copy.deepcopy(
        _case("source_identity_valid_present_propagation_and_semantic_sensitivity")
    )
    fixture["input"]["result"]["jobs"][0]["source_identity"] = invalid
    assert semantics.project_case(fixture) == {
        "case_id": fixture["id"],
        "reason": "invalid_projection",
        "status": "rejected",
    }


def test_source_identity_collision_dedupe_and_location_rules() -> None:
    collision = _case("source_identity_collision_rejected")
    assert collision["expected"] == {
        "case_id": collision["id"],
        "reason": "canonical_collision",
        "status": "suppressed",
    }

    base = copy.deepcopy(
        _case("source_identity_valid_present_propagation_and_semantic_sensitivity")
    )
    job = base["input"]["result"]["jobs"][0]
    base["input"]["result"]["jobs"].append(copy.deepcopy(job))
    assert semantics.project_case(base) == base["expected"]

    different_identity = copy.deepcopy(base)
    different_identity["input"]["result"]["jobs"][1]["source_identity"] = (
        "smartrecruiters:example:43"
    )
    assert semantics.project_case(different_identity)["reason"] == "canonical_collision"

    absent_identity = copy.deepcopy(base)
    del absent_identity["input"]["result"]["jobs"][1]["source_identity"]
    assert semantics.project_case(absent_identity)["reason"] == "canonical_collision"

    mixed_url_only = copy.deepcopy(base)
    mixed_url_only["input"]["result"]["urls"] = [job["url"]]
    assert semantics.project_case(mixed_url_only)["reason"] == "canonical_collision"

    misplaced = copy.deepcopy(base)
    misplaced["input"]["existing_projected_effects"]["targets"][0]["source_identity"] = (
        "smartrecruiters:example:42"
    )
    assert semantics.project_case(misplaced)["reason"] == "invalid_projection"

    invalid_existing = copy.deepcopy(base)
    invalid_existing["input"]["existing_projected_effects"]["job_effects"][0]["source_identity"] = (
        "not-namespaced"
    )
    assert semantics.project_case(invalid_existing)["reason"] == "invalid_projection"

    misplaced_content = copy.deepcopy(base)
    misplaced_content["input"]["result"]["jobs"][0]["content"]["source_identity"] = (
        "smartrecruiters:example:42"
    )
    assert semantics.project_case(misplaced_content)["reason"] == "invalid_projection"

    existing_collision = copy.deepcopy(base)
    existing = existing_collision["input"]["existing_projected_effects"]
    collision_url = "https://jobs.example.invalid/openings/source-identity-z"
    existing["urls_to_upsert"].append(collision_url)
    existing["content_hashes"].append(existing["content_hashes"][0])
    existing["job_effects"].append({**existing["job_effects"][0], "source_url": collision_url})
    existing["targets"].append({**existing["targets"][0], "url": collision_url})
    assert semantics.project_case(existing_collision)["reason"] == "canonical_collision"


@pytest.mark.parametrize(
    "value",
    [
        "https://user@jobs.example.invalid/role",
        "https://jobs.example.invalid:0/role",
        "https://jobs.example.invalid:65536/role",
        "http://127.0.0.1/role",
        "http://0177.0.0.1/role",
        "http://0x7f.0.0.1/role",
        "http://127.0x0.0.1/role",
        "http://2130706433/role",
        "https://jobs.example.invalid/../../role",
        "https://jöbs.example.invalid/role",
        "https://jobs.example.invalid./role",
        "https://jobs.example.invalid/role#bad%ZZ",
        "https://jobs.example.invalid\\role",
        "https://jobs.example.invalid/role with-space",
        "https://jobs.example.invalid/role\tcontrol",
    ],
)
def test_url_profile_rejects_the_closed_invalid_set(value: str) -> None:
    with pytest.raises(semantics.SemanticFailure) as caught:
        semantics.canonical_url(value)
    assert caught.value.reason == "invalid_url"


def test_locale_aliases_are_closed_and_canonical() -> None:
    for canonical in sorted(set(semantics.LOCALES.values())):
        assert semantics.canonical_locale(canonical) == canonical
    assert semantics.canonical_locale("EN_us") == "en-US"
    assert semantics.canonical_locale("de_ch") == "de-CH"
    with pytest.raises(semantics.SemanticFailure) as caught:
        semantics.canonical_locale("es-ES")
    assert caught.value.reason == "invalid_locale"


def test_absent_empty_order_and_one_byte_digest_semantics_are_preserved() -> None:
    absent = _case("absent_vs_empty")["expected"]
    assert absent["status"] == "projected"
    assert len(set(absent["projected_effects"]["content_hashes"])) == 2
    metadata_case = _case("ordered_metadata")
    input_value = metadata_case["input"]
    target = input_value["request"]["target_url"]
    actual = input_value["result"]["metadata_updates"]
    comparison = input_value["comparison_metadata_updates"]
    assert semantics.metadata_sha256(target, actual) != semantics.metadata_sha256(
        target, comparison
    )
    digest_case = _case("digest_sensitivity")
    expected_digest = digest_case["expected"]["projected_effects"]["content_hashes"][0]
    assert digest_case["input"]["comparison_content_sha256"] != expected_digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hybrid", True),
        ("truncated", True),
        ("filtered_count", 1),
        ("security_filtered_count", 1),
    ],
)
def test_each_gone_suppression_condition_keeps_safe_upserts(field: str, value: object) -> None:
    fixture = copy.deepcopy(_case("safe_monitor_url_only"))
    fixture["input"]["result"][field] = value
    result = semantics.project_case(fixture)
    assert result["status"] == "projected"
    assert result["projected_effects"]["gone_detection_allowed"] is False
    assert len(result["projected_effects"]["targets"]) == 2


def test_batch_metadata_order_is_lossless_and_sitemap_conflict_is_closed() -> None:
    fixture = copy.deepcopy(_case("monitor_batches_ordered"))
    original = semantics.project_case(fixture)
    fixture["input"]["batches"].reverse()
    reversed_result = semantics.project_case(fixture)
    assert original["status"] == reversed_result["status"] == "projected"
    assert (
        original["projected_effects"]["metadata_updates_sha256"]
        != (reversed_result["projected_effects"]["metadata_updates_sha256"])
    )
    assert _case("monitor_batches_sitemap_conflict")["expected"]["reason"] == (
        "canonical_collision"
    )


def test_protocol_and_privacy_are_consumed_as_preconditions() -> None:
    fixture = copy.deepcopy(_case("safe_scrape_projected"))
    fixture["input"]["preconditions"]["protocol_accepted"] = False
    assert semantics.project_case(fixture) == {
        "case_id": "safe_scrape_projected",
        "reason": "ineligible_history",
        "status": "suppressed",
    }
    fixture = copy.deepcopy(_case("safe_scrape_projected"))
    fixture["input"]["preconditions"]["privacy_status"] = "rejected"
    assert semantics.project_case(fixture) == {
        "case_id": "safe_scrape_projected",
        "reason": "privacy_rejected",
        "status": "suppressed",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("protocol_accepted", "true"),
        ("terminal_status", True),
        ("eligible_for_commit", 1),
        ("batches_complete", 1),
        ("privacy_status", False),
    ],
)
def test_every_precondition_type_is_checked_before_suppression(field: str, value: object) -> None:
    fixture = copy.deepcopy(_case("safe_scrape_projected"))
    fixture["input"]["preconditions"]["privacy_status"] = "rejected"
    fixture["input"]["preconditions"][field] = value
    assert semantics.project_case(fixture) == {
        "case_id": "safe_scrape_projected",
        "reason": "invalid_projection",
        "status": "rejected",
    }


def test_malformed_unicode_and_present_null_reject_without_throwing() -> None:
    fixture = copy.deepcopy(_case("safe_scrape_projected"))
    fixture["input"]["result"]["content"]["description_html"] = b"\xff"
    assert semantics.project_case(fixture) == {
        "case_id": "safe_scrape_projected",
        "reason": "invalid_projection",
        "status": "rejected",
    }
    for case_id in (
        "invalid_unicode_surrogate",
        "invalid_metadata_null",
        "invalid_localized_description_null",
    ):
        assert _case(case_id)["expected"] == {
            "case_id": case_id,
            "reason": "invalid_projection",
            "status": "rejected",
        }


def test_canonical_json_is_safe_literal_utf8_and_rejects_floats_and_surrogates() -> None:
    assert semantics.canonical_json({"z": "\u2028", "a": "é"}) == (
        b'{"a":"\xc3\xa9","z":"\xe2\x80\xa8"}'
    )
    for invalid in (1.5, "\ud800"):
        with pytest.raises(semantics.SemanticFailure) as caught:
            semantics.canonical_json({"value": invalid})
        assert caught.value.reason == "invalid_projection"
        assert caught.value.status == "rejected"


def test_set_permutations_dedupe_and_language_uses_the_closed_locale_table() -> None:
    first = {
        "description_html": "<p>Visible.</p>",
        "localizations": [],
        "locations": {"values": ["Zürich", "Basel", "Zürich"]},
        "skills": ["sql", "python", "sql"],
        "language": "EN_us",
    }
    second = {
        **first,
        "locations": {"values": ["Basel", "Zürich"]},
        "skills": ["python", "sql"],
        "language": "en-US",
    }
    assert semantics.canonical_job(first) == semantics.canonical_job(second)
    assert semantics.canonical_job(first)["language"] == "en-US"


def test_suppressed_and_rejected_outputs_are_closed_and_do_not_leak_input() -> None:
    canary = "SYNTHETIC_RAW_CANARY_DO_NOT_EMIT"
    for source in ("invalid_visible_content", "invalid_url", "locale_rejection"):
        fixture = copy.deepcopy(_case(source))
        fixture["input"]["raw_canary"] = canary
        result = semantics.project_case(fixture)
        assert set(result) == {"case_id", "reason", "status"}
        assert canary.encode() not in semantics.canonical_json(result)


def test_projection_and_generation_are_zero_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("semantics conformance attempted network access")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    for fixture in _manifest()["cases"]:
        assert semantics.project_case(fixture) == fixture["expected"]
    assert semantics.manifest_bytes() == MANIFEST_PATH.read_bytes()


def test_generator_is_deterministic_and_cli_check_is_clean() -> None:
    assert semantics.manifest_bytes() == semantics.manifest_bytes()
    completed = subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--check"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith("(54 cases)")
