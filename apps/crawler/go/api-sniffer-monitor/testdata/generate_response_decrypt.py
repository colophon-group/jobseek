"""Freeze the original initial-response AES projection without real secrets."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.core.monitors.api_sniffer import _apply_response_decrypt  # noqa: E402

KEY = "fixture-key-1234"
IV = "0123456789abcdef"


def encoded(value, suffix=True):
    text = json.dumps(value, ensure_ascii=False).encode()
    body = base64.b64encode(
        AES.new(KEY.encode(), AES.MODE_CBC, IV.encode()).encrypt(pad(text, 16))
    ).decode()
    return body + IV if suffix else body


payload = {
    "jobs": [{"id": "one", "title": "Engineer", "description": "<p>Systems</p>"}],
    "ordered": {"z": 2, "a": 1},
}
normal = {"before": 2, "Data": encoded(payload), "after": "retained"}
fixed = {"key": KEY, "iv_mode": "fixed:" + IV}
invalid_utf = (
    base64.b64encode(
        AES.new(KEY.encode(), AES.MODE_CBC, IV.encode()).encrypt(pad(b"\xff", 16))
    ).decode()
    + IV
)
bad_padding = (
    base64.b64encode(
        AES.new(KEY.encode(), AES.MODE_CBC, IV.encode()).encrypt(b"\x00" * 16)
    ).decode()
    + IV
)
cases = [
    ("suffix", normal, {"key": KEY}),
    ("fixed", {"Data": encoded(payload, False)}, fixed),
    ("array", [normal], {"key": KEY}),
    ("missing", {"other": 1}, {"key": KEY}),
    ("null", {"Data": None}, {"key": KEY}),
    ("number", {"Data": 42}, {"key": KEY}),
    ("empty", {"Data": ""}, {"key": KEY}),
    ("short", {"Data": "short"}, {"key": KEY}),
    ("invalid-base64", {"Data": "%%%%" + IV}, {"key": KEY}),
    ("invalid-utf8", {"Data": invalid_utf}, {"key": KEY}),
    ("invalid-padding", {"Data": bad_padding}, {"key": KEY}),
    ("base64-whitespace", {"Data": " \n".join([normal["Data"][:-16], IV])}, {"key": KEY}),
    ("base64-ignored-ascii", {"Data": normal["Data"][:-16] + "!%?" + IV}, {"key": KEY}),
    ("unicode-iv", {"Data": normal["Data"][:-16] + "é" * 16}, {"key": KEY}),
    ("wrong-key", normal, {"key": "wrong-key-123456"}),
]
output = []
for name, data, config in cases:
    output.append(
        {
            "name": name,
            "input": data,
            "config": config,
            "expected": _apply_response_decrypt(data, config),
        }
    )
Path(__file__).with_name("python_response_decrypt.json").write_text(
    json.dumps(output, indent=2, ensure_ascii=False) + "\n"
)
print("Frozen15 original response decrypt contracts using synthetic keys")
