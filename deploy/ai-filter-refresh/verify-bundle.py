"""Verify the immutable worker received from the exact reviewed CI revision."""
import hashlib
import json
from pathlib import Path
import re
import sys


def verify(root: Path, revision: str, manifest_hash: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{40}", revision) or not re.fullmatch(r"[a-f0-9]{64}", manifest_hash):
        raise ValueError("invalid worker identity")
    manifest_path = root / "manifest.json"
    worker_path = root / "worker.mjs"
    if root.is_symlink() or manifest_path.is_symlink() or worker_path.is_symlink():
        raise ValueError("worker bundle contains symlinks")
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != manifest_hash:
        raise ValueError("worker manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("version") != 1 or manifest.get("sourceRevision") != revision
        or manifest.get("sourceClean") is not True
        or not re.fullmatch(r"node:24-alpine@sha256:[a-f0-9]{64}", manifest.get("runtimeImage", ""))
        or hashlib.sha256(worker_path.read_bytes()).hexdigest() != manifest.get("workerSha256")):
        raise ValueError("worker identity mismatch")
    return manifest


if __name__ == "__main__":
    try:
        verify(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
    except Exception:
        print("Narrowed worker bundle verification failed", file=sys.stderr)
        sys.exit(1)
