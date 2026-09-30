"""Bundle pinned Go dependency notices from the verified local module cache."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
output = root / "licenses"
output.mkdir(exist_ok=True)
raw = subprocess.check_output(["go", "list", "-m", "-json", "all"], cwd=root, text=True)
modules = []
decoder = json.JSONDecoder()
while raw.strip():
    module, end = decoder.raw_decode(raw.lstrip())
    raw = raw.lstrip()[end:]
    if module.get("Main"):
        continue
    source = module.get("Dir")
    if not source:
        continue
    destination = output / (module["Path"].replace("/", "_") + "@" + module["Version"])
    destination.mkdir(exist_ok=True)
    files = []
    for notice in sorted(Path(source).iterdir()):
        if notice.is_file() and (
            notice.name.upper().startswith("LICENSE")
            or notice.name.upper().startswith("COPYING")
            or notice.name.upper().startswith("NOTICE")
        ):
            target = destination / notice.name
            shutil.copyfile(notice, target)
            files.append(
                {
                    "path": str(target.relative_to(output)),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )
    if not files:
        raise RuntimeError(f"missing dependency license: {module['Path']}")
    modules.append({"module": module["Path"], "version": module["Version"], "notices": files})
(output / "manifest.json").write_text(json.dumps(modules, indent=2) + "\n")
print(f"Bundled dependency notices for {len(modules)} pinned modules")
