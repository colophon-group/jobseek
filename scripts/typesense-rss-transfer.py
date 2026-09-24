"""Retrieve one encrypted corpus; neither the URL nor password is logged."""

from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

os.umask(0o077)
config = json.loads(os.environ.pop("LAB_TRANSFER"))
root = Path(os.environ["RUNNER_TEMP"])
cipher, archive = root / "rss-input.enc", root / "rss-input.tar"
expected = config["cipher_sha256"]
received = hashlib.sha256()
try:
    with (
        urllib.request.urlopen(config["url"], timeout=180) as response,
        cipher.open("wb") as out,
    ):
        while chunk := response.read(1024**2):
            out.write(chunk)
            received.update(chunk)
except Exception:
    raise SystemExit(
        "Encrypted corpus download failed; transfer details suppressed"
    ) from None
assert received.hexdigest() == expected, "Encrypted corpus digest mismatch"
subprocess.run(
    [
        "openssl",
        "enc",
        "-d",
        "-aes-256-cbc",
        "-pbkdf2",
        "-iter",
        "200000",
        "-pass",
        "stdin",
        "-in",
        str(cipher),
        "-out",
        str(archive),
    ],
    input=(config["password"] + "\n").encode(),
    check=True,
)
config.clear()
cipher.unlink()
output = root / "rss-input"
output.mkdir(mode=0o700)
with tarfile.open(archive, "r:") as source:
    members = source.getmembers()
    assert {m.name for m in members} == {
        "schema.json",
        "corpus.json",
        "postings.jsonl.gz",
    }
    assert len(members) == 3 and all(m.isfile() for m in members)
    for member in members:
        with (
            source.extractfile(member) as data,
            (output / member.name).open("wb") as out,
        ):
            shutil.copyfileobj(data, out)
archive.unlink()
print("Encrypted corpus verified and unpacked; raw data excluded from result artifacts")
