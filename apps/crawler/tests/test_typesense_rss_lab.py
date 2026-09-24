"""Reject incomplete imports instead of reporting misleading memory evidence."""

from __future__ import annotations

import gzip
import importlib.util
import io
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "rss_lab", Path(__file__).resolve().parents[3] / "scripts/typesense-rss-lab.py"
)
assert SPEC and SPEC.loader
rss_lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rss_lab)


def test_missing_write_queue_metric_cannot_be_reported_as_ready(monkeypatch):
    monkeypatch.setattr(rss_lab, "api", lambda *_args, **_kwargs: {})
    with pytest.raises(RuntimeError, match="valid pending-write count"):
        rss_lab.wait_for_writes({"port": 1})


@pytest.mark.parametrize(
    ("acknowledgements", "stored_count", "error"),
    [
        ([{"success": True}], 2, "expected 2 acknowledgements, received 1"),
        ([{"success": True}, {"success": False, "error": "invalid field"}], 1, "invalid field"),
        ([{"success": True}, {"success": True}], 1, "Expected 2 stored documents, found 1"),
    ],
)
def test_import_rejects_incomplete_evidence(
    tmp_path, monkeypatch, acknowledgements, stored_count, error
):
    source = tmp_path / "source.jsonl.gz"
    with gzip.open(source, "wt") as output:
        output.write('{"id":"a"}\n{"id":"b"}\n')
    response = "\n".join(json.dumps(row) for row in acknowledgements).encode()
    monkeypatch.setattr(
        rss_lab.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(response)
    )
    monkeypatch.setattr(rss_lab, "api", lambda *_args: {"num_documents": stored_count})
    with pytest.raises(RuntimeError, match=error):
        rss_lab.import_documents({"port": 1}, source)


def test_resume_hashes_complete_source_and_replays_uncertain_suffix(tmp_path, monkeypatch):
    source = tmp_path / "source.jsonl.gz"
    raw = b'{"id":"a"}\n{"id":"b"}\n'
    with gzip.open(source, "wb") as output:
        output.write(raw)
    requests = []

    def respond(request, **_kwargs):
        requests.append(request.data)
        return io.BytesIO(b'{"success":true}')

    monkeypatch.setattr(rss_lab.urllib.request, "urlopen", respond)
    monkeypatch.setattr(rss_lab, "api", lambda *_args: {"num_documents": 2})
    result = rss_lab.import_documents({"port": 1}, source, skip_documents=1)
    assert requests == [b'{"id": "b"}\n']
    assert result == {
        "documents": 2,
        "resumed_after": 1,
        "source_sha256": rss_lab.hashlib.sha256(raw).hexdigest(),
    }


def test_import_matches_producer_unicode_transport_without_losing_integer_precision(
    tmp_path, monkeypatch
):
    document = {"id": "a", "title": "工程师", "candidate_order_hi": 9223372036854775807}
    raw = (json.dumps(document, ensure_ascii=False) + "\n").encode()
    source = tmp_path / "source.jsonl.gz"
    with gzip.open(source, "wb") as output:
        output.write(raw)
    requests = []

    def respond(request, **_kwargs):
        requests.append(request.data)
        return io.BytesIO(b'{"success":true}')

    monkeypatch.setattr(rss_lab.urllib.request, "urlopen", respond)
    monkeypatch.setattr(rss_lab, "api", lambda *_args: {"num_documents": 1})
    result = rss_lab.import_documents({"port": 1}, source)
    assert requests[0].isascii()
    assert json.loads(requests[0]) == document
    assert requests[0] == (json.dumps(document) + "\n").encode()
    assert result["source_sha256"] == rss_lab.hashlib.sha256(raw).hexdigest()
