"""Native taxonomy ownership, resident lifecycle, and bridge semantics."""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
from pathlib import Path

import pytest

from src.processing.cpu import (
    _extract_experience_fields,
    _resolve_occupation_seniority,
    _resolve_technology_ids,
)
from src.runtime import job_enrichment_go as bridge
from src.shared import html_normalize, langdetect

MODULE = Path(__file__).resolve().parents[1] / "go/job-enrichment"
DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    path = tmp_path_factory.mktemp("go-enrichment") / "enrichment"
    # CI verifies Go race/vet independently; this fixture builds the bridge target.
    subprocess.run(
        ["go", "build", "-o", str(path), "./cmd/live"], cwd=MODULE, capture_output=True, check=True
    )
    return str(path)


@pytest.fixture
def native(binary, monkeypatch):
    client = bridge.GoJobEnrichment(binary, DATA)
    monkeypatch.setattr(bridge, "_client", client)
    monkeypatch.setenv("JOB_ENRICHMENT_ENGINE", "go")
    yield client
    client.close()


def test_native_and_legacy_title_id_semantics(native, monkeypatch):
    occ = {"software-engineer": 0, "data-scientist": 20}
    sen = {"senior": 30, "intern": 40, "director": 50}
    cases = [
        None,
        [],
        "Software Engineer",
        [None, 1, "Senior Mystery", "Data Scientist"],
        ["CTO", "Software Engineer"],
        ["Senior Software Engineer", "Head of Engineering"],
    ]
    for titles in cases:
        for employment in [None, "full_time", "Internship", "Apprenticeship"]:
            monkeypatch.setenv("JOB_ENRICHMENT_ENGINE", "python")
            expected = _resolve_occupation_seniority(titles, occ, sen, employment_type=employment)
            monkeypatch.setenv("JOB_ENRICHMENT_ENGINE", "go")
            assert (
                _resolve_occupation_seniority(titles, occ, sen, employment_type=employment)
                == expected
            )
    assert _resolve_technology_ids(
        "<p>Python &amp; Java SQL</p>", {"python": 4, "java": 4, "sql": 0}
    ) == [0, 4]
    assert _resolve_technology_ids("Python", {}) is None


def test_one_resident_serializes_concurrent_calls(native):
    first = native.request("technology", description="Python")
    assert "python" in first["technologies"]
    child = native.proc
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: native.request("technology", description="Python"), range(40))
        )
    assert len({r["id"] for r in results}) == 40
    assert native.proc is child and child.poll() is None
    native.close()
    assert child.poll() == 0


def test_experience_bridge_uses_resident_for_all_python_cases(native):
    for case in json.loads((MODULE / "testdata/python_experience.json").read_text()):
        assert _extract_experience_fields(case["text"]) == (case["min"], case["max"])
    child = native.proc
    assert child is not None and child.poll() is None
    assert _resolve_technology_ids("Python", {"python": 1}) == [1]
    assert native.proc is child


def test_shared_html_owns_full_oracle_without_python_fallback(native, monkeypatch):
    cases = json.loads((MODULE / "testdata/python_html.json").read_text())["cases"]
    for case in cases:
        assert html_normalize._normalize_description_html_python(case["text"]) == case["html"]

    def reject_python(_):
        raise AssertionError("Go normalization called the Python parser")

    monkeypatch.setattr(html_normalize, "_normalize_description_html_python", reject_python)
    assert html_normalize.normalize_description_html(None) is None
    for case in cases:
        assert html_normalize.normalize_description_html(case["text"]) == case["html"]
    child = native.proc
    assert child is not None and child.poll() is None
    assert _resolve_technology_ids("Python", {"python": 1}) == [1]
    assert native.proc is child


def test_html_response_larger_than_taxonomy_bound_keeps_same_resident(native):
    # Escaping a delimiter expands even a small input beyond the old 1 MiB
    # response bound. Never truncate normalized HTML or silently change hashes.
    raw = "&" * 300_000
    assert html_normalize.normalize_description_html(raw) == "&amp;" * 300_000
    child = native.proc
    assert _extract_experience_fields("5 years experience") == (5, None)
    assert native.proc is child


def test_html_request_bound_reaps_before_next_operation(native, monkeypatch):
    native.request("technology", description="Python")
    child = native.proc
    monkeypatch.setattr(bridge, "_MAX_REQUEST", 100)
    with pytest.raises(ValueError, match="request exceeds bound"):
        html_normalize.normalize_description_html("X" * 200)
    assert native.proc is None and child.poll() is not None
    assert _resolve_technology_ids("Python", {"python": 1}) == [1]
    assert native.proc is not child


def test_html_response_bound_reaps_without_truncation(native, monkeypatch):
    monkeypatch.setattr(bridge, "_MAX_HTML_RESPONSE", 150)
    with pytest.raises(ValueError, match="response exceeds bound"):
        html_normalize.normalize_description_html("&" * 100)
    assert native.proc is None


def test_html_parse_failure_reaps_without_python_fallback(native, monkeypatch):
    def reject_python(_):
        raise AssertionError("failed Go normalization called Python")

    monkeypatch.setattr(html_normalize, "_normalize_description_html_python", reject_python)
    # The Go HTML5 parser bounds the open-element stack at 512 nodes.
    with pytest.raises(RuntimeError, match="rejected the request"):
        html_normalize.normalize_description_html("<div>" * 600 + "X")
    assert native.proc is None
    assert _resolve_technology_ids("Python", {"python": 1}) == [1]


@pytest.mark.parametrize("result", [{}, {"normalized_html": 1}, {"normalized_html": ""}])
def test_invalid_normalized_html_is_rejected(monkeypatch, result):
    class Broken:
        def request(self, *args, **kwargs):
            return result

    monkeypatch.setattr(bridge, "client", Broken)
    with pytest.raises(ValueError, match="Go normalized HTML"):
        bridge.normalize_html("<p>Hello</p>")


def test_failure_reaps_and_does_not_fall_back(tmp_path, monkeypatch):
    binary = tmp_path / "stall"
    binary.write_text(
        "#!/usr/bin/env python3\nimport json,time,sys\n"
        "print(json.dumps({'ready':True,'protocol':1}),flush=True)\n"
        "sys.stdin.readline()\ntime.sleep(30)\n"
    )
    binary.chmod(0o755)
    children = []
    popen = subprocess.Popen

    def capture_child(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(bridge.subprocess, "Popen", capture_child)
    client = bridge.GoJobEnrichment(str(binary), DATA, timeout=1)
    with pytest.raises(TimeoutError):
        client.request("technology", description="Python")
    assert client.proc is None
    assert client.sequence == 1  # The handshake completed before the request stalled.
    assert len(children) == 1 and children[0].poll() is not None


def test_fork_does_not_terminate_parent_resident(native):
    import multiprocessing

    native.request("technology", description="Python")
    parent = native.proc
    context = multiprocessing.get_context("fork")
    output, child_end = context.Pipe(duplex=False)

    def child():
        result = native.request("technology", description="Python")
        child_end.send((native.proc.pid, result["technologies"]))
        native.close()
        child_end.close()

    process = context.Process(target=child)
    process.start()
    child_end.close()
    try:
        assert output.poll(10)
        pid, slugs = output.recv()
        assert pid != parent.pid and "python" in slugs
        process.join(5)
        assert process.exitcode == 0
        assert parent.poll() is None
        assert "python" in native.request("technology", description="Python")["technologies"]
    finally:
        if process.is_alive():
            process.kill()
            process.join()
        output.close()


def test_installed_protocol_parity(binary):
    subprocess.run(
        [os.sys.executable, str(MODULE / "testdata/verify_installed.py"), binary, str(DATA)],
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_unknown_engine_is_rejected(monkeypatch):
    monkeypatch.setenv("JOB_ENRICHMENT_ENGINE", "typo")
    with pytest.raises(ValueError):
        bridge.enabled()


def test_shared_language_matches_oracle_without_loading_python_model(native, monkeypatch):
    cases = json.loads((MODULE / "testdata/python_language.json").read_text())["cases"]
    for case in cases:
        assert langdetect._detect_language_python(case["text"]) == case["language"]
        assert langdetect._detect_all_languages_python(case["text"]) == case["languages"]

    def reject_python(*args, **kwargs):
        raise AssertionError("Go language called the Python detector")

    monkeypatch.setattr(langdetect, "detect", reject_python)
    for case in cases:
        assert langdetect.detect_language(case["text"]) == case["language"]
        assert langdetect.detect_all_languages(case["text"]) == case["languages"]
    child = native.proc
    assert _resolve_technology_ids("Python", {"python": 1}) == [1]
    assert native.proc is child


def test_language_request_failure_does_not_become_inconclusive_success(native, monkeypatch):
    def reject_python(*args, **kwargs):
        raise AssertionError("failed Go language called Python")

    monkeypatch.setattr(langdetect, "detect", reject_python)
    monkeypatch.setattr(bridge, "_MAX_REQUEST", 100)
    with pytest.raises(ValueError, match="request exceeds bound"):
        langdetect.detect_all_languages("X" * 200)
    assert native.proc is None


@pytest.mark.parametrize("result", [{}, {"language": 1}, {"language": ""}, {"language": "EN"}])
def test_invalid_go_primary_language_is_rejected(monkeypatch, result):
    class Broken:
        def request(self, *args, **kwargs):
            return result

    monkeypatch.setattr(bridge, "client", Broken)
    with pytest.raises(ValueError, match="Go language"):
        bridge.detect_language("description")


@pytest.mark.parametrize("values", [None, "en", [1], ["EN"], ["en", "en"], [""]])
def test_invalid_go_all_languages_are_rejected(monkeypatch, values):
    class Broken:
        def request(self, *args, **kwargs):
            return {"languages": values}

    monkeypatch.setattr(bridge, "client", Broken)
    with pytest.raises(ValueError, match="Go languages"):
        bridge.detect_all_languages("description")


def test_go_language_cold_process_does_not_import_python_detector(binary):
    script = """
import sys
from pathlib import Path
from src.runtime import job_enrichment_go as bridge
from src.shared.langdetect import detect_language, detect_all_languages
bridge._client = bridge.GoJobEnrichment(sys.argv[1], Path(sys.argv[2]))
assert detect_language("This is a job posting written in English.") == "en"
text = "We are looking for a software engineer to join our team. " * 12
assert detect_all_languages(text) == ["en"]
assert not any(name.startswith(("fast_langdetect", "fasttext")) for name in sys.modules)
bridge.close_client()
"""
    subprocess.run(
        [os.sys.executable, "-c", script, binary, str(DATA)],
        env={**os.environ, "JOB_ENRICHMENT_ENGINE": "go"},
        check=True,
        capture_output=True,
        timeout=15,
    )


def test_shared_salary_owns_every_family_and_eur_without_python_fallback(native, monkeypatch):
    from dataclasses import asdict

    from src.core import salary_extract
    from src.processing import cpu

    cases = json.loads((MODULE / "testdata/python_salary.json").read_text())

    def reject_python(*_):
        raise AssertionError("Go salary called the Python extractor")

    monkeypatch.setattr(salary_extract, "_extract_salary_python", reject_python)
    monkeypatch.setattr(salary_extract, "_extract_salary_unified_python", reject_python)
    monkeypatch.setattr(salary_extract, "_parse_salary_text_python", reject_python)
    monkeypatch.setattr(cpu, "_extract_salary_fields_python", reject_python)
    for case in cases:
        text = case["text"]
        assert [asdict(r) for r in salary_extract.extract_salary(text)] == case["ranges"]
        result = salary_extract.extract_salary_unified(text)
        assert (asdict(result) if result else None) == case["unified"]
        assert salary_extract.parse_salary_text(text) == case["parsed"]
        expected = case["unified"]
        assert cpu._extract_salary_fields(text, case["rates"]) == (
            (
                expected["min"],
                expected["max"],
                expected["currency"],
                expected["period"],
                case["eur"],
            )
            if expected
            else (None, None, None, None, None)
        )
    assert cpu._extract_salary_fields(None, {}) == (None,) * 5
    child = native.proc
    assert child is not None and child.poll() is None
    assert _resolve_technology_ids("Python", {"python": 1}) == [1]
    assert native.proc is child


def test_salary_failure_reaps_without_python_fallback(native, monkeypatch):
    from src.core.salary_extract import extract_salary

    with pytest.raises(RuntimeError, match="rejected"):
        extract_salary("USA, NV, Sparks - " + "9" * 400 + " - 30 USD hourly")
    assert native.proc is None
    assert extract_salary("Salary $120000/year")[0].min == 120000


@pytest.mark.parametrize(
    "value", [None, {}, {"ranges": [], "unified": {}, "parsed": None, "eur": None}]
)
def test_invalid_salary_response_fails_explicitly(native, monkeypatch, value):
    monkeypatch.setattr(native, "request", lambda *_args, **_kw: {"salary": value})
    with pytest.raises(ValueError, match="Go salary"):
        bridge.salary_result("Salary $120000/year")
