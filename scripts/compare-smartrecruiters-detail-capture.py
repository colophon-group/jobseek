"""Compare a retained SmartRecruiters API response without any publisher fetch."""

import argparse
import base64
import dataclasses
import hashlib
import json
import stat
import subprocess
from pathlib import Path

from src.core.scrapers.smartrecruiters import _parse_detail
from src.runtime.smartrecruiters_go_detail import eligible

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("capture", type=Path)
parser.add_argument("binary", type=Path)
parser.add_argument("--completion-sha256")
args = parser.parse_args()
assert stat.S_IMODE(args.capture.stat().st_mode) == 0o600, "capture must be mode 0600"
capture = json.loads(args.capture.read_text())
body = base64.b64decode(capture["body_base64"], validate=True)
assert len(body) <= 1 << 20, "detail body exceeds bound"
assert hashlib.sha256(body).hexdigest() == capture["body_sha256"]
token, posting_id = eligible(capture["url"], "smartrecruiters", None)
assert capture["endpoint"] == (
    f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{posting_id}"
)
posting = json.loads(body)
expected = dataclasses.asdict(_parse_detail(posting))
result = subprocess.run(
    [str(args.binary.resolve())],
    input=json.dumps(
        {"mode": "parse-detail", "posting": posting}, ensure_ascii=False
    ).encode(),
    capture_output=True,
    timeout=30,
    check=True,
)
parsed = json.loads(result.stdout)
actual = {key: parsed[key] for key in expected}
assert actual == expected, {
    "different_fields": [key for key in expected if actual[key] != expected[key]]
}
fields_hash = hashlib.sha256(
    json.dumps(
        expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
).hexdigest()
if args.completion_sha256 is not None:
    assert fields_hash == args.completion_sha256, "natural completion hash differs"
print(
    json.dumps(
        {
            "source_url": capture["url"],
            "board_id": capture["board_id"],
            "response_bytes": len(body),
            "response_sha256": capture["body_sha256"],
            "parser_fields_equal": True,
            "populated_fields": [
                key for key, value in expected.items() if value is not None
            ],
            "expected_complete_fields_sha256": fields_hash,
            "completion_log_hash_match": "match"
            if args.completion_sha256
            else "pending readback",
        }
    )
)
