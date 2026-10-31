from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from admission.native_lane.captured_inputs import CaptureRejected, bound_config, prepare, qualify
from src.core.jsonld import parse_html

URL = "https://careers.example.test/jobs/1"
HTML = (
    b'<!doctype html><html><head><script type="application/ld+json">'
    b'{"@type":"JobPosting","title":"Engineer",'
    b'"description":"<p>Build reliable systems.</p>"}'
    b"</script></head><body>Career</body></html>"
)
REQUEST = {"url": URL, "method": "GET", "status": 200, "content_type": "text/html"}


def expected():
    return {**asdict(parse_html(HTML.decode())), "url": URL}


def board():
    return {
        "configs": {
            "candidate": {
                "scraper_type": "json-ld",
                "scraper_config": {},
                "scraper_run": {"ran_at": "2026-10-01T12:00:10Z"},
            }
        }
    }


def test_capture_reproduces_saved_fields_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline replay attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    record = qualify(HTML, expected(), [REQUEST], "json-ld", {})
    assert record["expected"]["title"] == "Engineer"
    assert record["expected"]["description"] == "<p>Build reliable systems.</p>"
    assert record["html_bytes"] == len(HTML)


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("fragment", "not_full_response_document"),
        ("request_url", "successful_source_request_missing"),
        ("request_status", "successful_source_request_missing"),
        ("request_method", "successful_source_request_missing"),
        ("title", "recorded_output_mismatch"),
        ("description", "recorded_output_mismatch"),
        ("credentials", "source_url"),
        ("non_https", "source_url"),
    ],
)
def test_capture_rejects_weak_provenance_or_changed_output(damage, reason):
    body, saved, request = HTML, expected(), dict(REQUEST)
    if damage == "fragment":
        body = b"<p>Build reliable systems.</p>"
    elif damage == "request_url":
        request["url"] = URL + "/other"
    elif damage == "request_status":
        request["status"] = 403
    elif damage == "request_method":
        request["method"] = "POST"
    elif damage in ("title", "description"):
        saved[damage] = "Different recorded value"
    elif damage == "credentials":
        saved["url"] = "https://private:secret@careers.example.test/jobs/1"
    elif damage == "non_https":
        saved["url"] = URL.replace("https:", "http:")
    with pytest.raises(CaptureRejected, match="^" + reason + "$"):
        qualify(body, saved, [request], "json-ld", {})


def test_config_binding_rejects_ambiguity_and_uncaptured_browser_dependencies():
    value = board()
    assert bound_config(value, "run-20261001T120000") == ("json-ld", {})
    selected = value["configs"]["candidate"]
    value["configs"]["duplicate"] = dict(selected)
    with pytest.raises(CaptureRejected, match="^config_run_binding$"):
        bound_config(value, "run-20261001T120000")
    del value["configs"]["duplicate"]
    selected["scraper_config"] = {"render": True}
    with pytest.raises(CaptureRejected, match="^browser_dependencies_not_captured$"):
        bound_config(value, "run-20261001T120000")


def test_prepare_writes_private_inputs_and_only_a_sanitized_summary(tmp_path: Path):
    workspace = tmp_path / "workspace"
    company = workspace / "private-company"
    run = company / "artifacts/main/scraper/run-20261001T120000"
    run.mkdir(parents=True)
    (company / "boards").mkdir()
    # JSON is valid YAML and keeps this fixture's serialization unambiguous.
    (company / "boards/main.yaml").write_text(json.dumps(board()))
    (run / "sample-0.html").write_bytes(HTML)
    (run / "sample-0.json").write_text(json.dumps(expected()))
    (run / "http_log.json").write_text(json.dumps([REQUEST]))
    destination = tmp_path / "inputs"
    report = prepare(workspace, destination)
    assert report["counts"] == {"paired_html": 1, "qualified": 1}
    assert report["qualified_types"] == {"json-ld": 1}
    assert report["network_requests"] == 0 and not report["whole_lane_measurement"]
    assert (destination / "0000.html").read_bytes() == HTML
    assert destination.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in destination.iterdir())
    assert URL not in json.dumps(report) and "private-company" not in json.dumps(report)
    with pytest.raises(FileExistsError):
        prepare(workspace, destination)


def test_prepare_rejects_symlink_outside_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    run = workspace / "company/artifacts/main/scraper/run-20261001T120000"
    run.mkdir(parents=True)
    external = tmp_path / "external.html"
    external.write_bytes(HTML)
    (run / "sample-0.html").symlink_to(external)
    (run / "sample-0.json").write_text(json.dumps(expected()))
    report = prepare(workspace, tmp_path / "inputs")
    assert report["counts"] == {"paired_html": 1, "source_escape": 1}


def test_captured_mode_preserves_all_response_bytes_and_extraction_fields():
    # A separate interpreter exercises the same import-time input selection as
    # the controller, fixture and driver. Keep original page data out of test
    # failures; assert only fixed categories and counts in the child.
    script = """
import hashlib, socket
from admission.native_lane import workload as w
from admission.native_lane.captured_inputs import qualify
from admission.native_lane.fixture_server import FixtureState
def forbidden(*args, **kwargs):
    raise RuntimeError('network access forbidden')
socket.create_connection = forbidden
rows = w.jobs()
assert len(rows) == len({r['url'] for r in rows}) == 72
assert {r['scraper_type'] for r in rows} == {'dom', 'json-ld'}
assert not any(b['monitor_seeded'] or b['scraper_needs_browser'] for b in w.boards())
state = FixtureState()
assert len(state.details) == len(rows)
for row in rows:
    body = w.detail_body(row)
    assert hashlib.sha256(body).hexdigest() == row['html_sha256']
    assert state.response(row['origin'], row['path'])[0] == body
    expected = {**row['expected'], 'url': row['url']}
    requests = [{'url': row['url'], 'method': 'GET', 'status': 200, 'content_type': 'text/html'}]
    qualify(body, expected, requests, row['scraper_type'], row['scraper_config'])
print('captured_cases=72; scraper_families=2; network_requests=0')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "NATIVE_LANE_INPUT_MODE": "captured", "DOM_GO_PARSE_ENABLED": "0"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, "captured response or extraction contract failed"
    assert result.stdout.strip() == "captured_cases=72; scraper_families=2; network_requests=0"
