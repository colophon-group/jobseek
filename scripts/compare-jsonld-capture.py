"""Compare retained JSON-LD parser bytes only. Never fetch a publisher."""

import argparse
import base64
import dataclasses
import hashlib
import json
import stat
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from src.core.jsonld import parse_rendered_html
from src.core.scrapers.jsonld import _selected_description

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("capture", type=Path)
parser.add_argument("binary", type=Path)
parser.add_argument(
    "--configs", type=Path, default=Path("/tmp/jobseek-jsonld-detail-configs.json")
)
parser.add_argument(
    "--baseline", type=Path, default=Path("/tmp/jobseek-jsonld-detail-predeploy.json")
)
parser.add_argument("--completion-sha256")
args = parser.parse_args()
for path in (args.capture, args.configs, args.baseline):
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, (
        f"input must be mode 0600: {path}"
    )
capture_path = args.capture
capture = json.loads(capture_path.read_text())
body = base64.b64decode(capture["body_base64"], validate=True)
assert hashlib.sha256(body).hexdigest() == capture["body_sha256"]
assert capture["encoding"] == "utf-8"
html = body.decode("utf-8")
url = capture["url"]
configs = {r["id"]: r["scraper_config"] for r in json.loads(args.configs.read_text())}
baseline = json.loads(args.baseline.read_text())
matches = [j for j in baseline["jobs"] if j["source_url"] == url]
if not matches and urlsplit(url).query == "in_iframe=1":
    target = urlsplit(url)
    for posting in baseline["jobs"]:
        parsed = urlsplit(posting["source_url"])
        if (parsed.scheme, parsed.netloc, parsed.path) == (
            target.scheme, target.netloc, target.path
        ):
            matches.append(posting)
assert len({j["board_id"] for j in matches}) == 1, "ambiguous or absent source board"
job = matches[0]
config = configs[job["board_id"]] or {}
expected = dataclasses.asdict(parse_rendered_html(url, config, html))
result = subprocess.run(
    [str(args.binary.resolve()), "--parse"],
    input=json.dumps(
        {"url": url, "html": html, "config": config}, ensure_ascii=False
    ).encode(),
    capture_output=True,
    timeout=30,
    check=True,
)
actual = json.loads(result.stdout)
assert actual == expected, {
    "different_fields": [
        key for key in set(actual) | set(expected) if actual.get(key) != expected.get(key)
    ]
}
full = dict(expected)
if config.get("description_selector") is not None:
    full["description"] = _selected_description(html, config["description_selector"])
full_hash = hashlib.sha256(
    json.dumps(full, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
).hexdigest()
if args.completion_sha256 is not None:
    assert full_hash == args.completion_sha256, (
        "natural completion fields differ from Python oracle"
    )
print(
    json.dumps(
        {
            "source_url": job["source_url"],
            "retained_fetch_url": url,
            "posting_id": job["id"],
            "board_id": job["board_id"],
            "html_bytes": len(body),
            "html_sha256": capture["body_sha256"],
            "parser_fields_equal": True,
            "populated_fields": [k for k, v in full.items() if v is not None],
            "expected_complete_fields_sha256": full_hash,
            "completion_log_hash_match": "match"
            if args.completion_sha256
            else "pending readback",
        }
    )
)
