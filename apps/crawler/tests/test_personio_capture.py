from __future__ import annotations

import hashlib
import stat

from src.core.monitors import personio_capture


def test_capture_uses_exact_first_response_bytes_and_private_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONIO_CAPTURE_SLUG", "acme")
    monkeypatch.setattr(personio_capture, "_CAPTURE_DIR", tmp_path)
    first = b"<workzag-jobs><position><id>42</id></position></workzag-jobs>"
    personio_capture.capture_personio_response("acme", "de", "en", first)
    personio_capture.capture_personio_response("acme", "de", "en", b"second response")
    suffix = hashlib.sha256(b"acme").hexdigest()[:16]
    path = tmp_path / f"jobseek-personio-{suffix}-de-en.xml"
    assert path.read_bytes() == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    personio_capture.capture_personio_response("other", "de", "en", first)
    assert len(list(tmp_path.iterdir())) == 1
