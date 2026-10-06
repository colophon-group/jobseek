"""Freeze ADP DOCX fallback and malformed-input behavior from actual Python."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

from src.core.scrapers.adp import docx_to_html

namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
body = "<w:body><w:p><w:r><w:t>Build &amp; ship</w:t></w:r></w:p></w:body>"
document = f'<w:document xmlns:w="{namespace}">{body}</w:document>'
cases = []


def record(name, xml=None, content=None):
    if content is None:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
            info = zipfile.ZipInfo("word/document.xml" if xml is not None else "other.xml")
            info.date_time = (2026, 10, 6, 0, 0, 0)
            archive.writestr(info, xml or "missing")
        content = stream.getvalue()
    cases.append(
        dict(name=name, body=base64.b64encode(content).decode(), expected=docx_to_html(content))
    )


record("valid", document)
record("harmless-doctype", "<!DOCTYPE w:document>" + document)
record("invalid-doctype", "<!DOCTYPE w:document nonsense>" + document)
record("external-doctype", '<!DOCTYPE w:document SYSTEM "https://example.com/x">' + document)
record(
    "entity",
    '<!DOCTYPE w:document [<!ENTITY x "expanded">]>' + document.replace("Build &amp; ship", "&x;"),
)
record("root-body", f'<w:body xmlns:w="{namespace}"><w:p><w:r><w:t>Root</w:t></w:r></w:p></w:body>')
record("trailing-garbage", document + "garbage")
record("two-roots", document + document)
record("missing-document")
record("invalid-zip", content=b"not a zip")
(Path(__file__).parent / "python_adp_docx.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print("actual Python ADP DOCX cases", len(cases))
