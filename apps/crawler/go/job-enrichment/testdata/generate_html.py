"""Freeze canonical HTML bytes from the retained Python/Lexbor implementation."""

from __future__ import annotations

import html.entities
import json
import random
from pathlib import Path

from src.shared.html_normalize import (
    _ALLOWED_TAGS,
    _DROP_SUBTREE_TAGS,
    _normalize_description_html_python,
)

cases = [
    "",
    " \t\n\u001c\u00a0 ",
    "plain text",
    "a \"b\" 'c' > < &",
    '<p class="copy">Join <a href="https://example.com">here</a></p>',
    "Use &lt;script&gt;alert(1)&lt;/script&gt; as an example.",
    "<div><section><p>One</p><p>Two</p></section></div>",
    "<p> &amp; &gt; &lt; &quot; &apos; &nbsp; &#13; &#0; </p>",
    "<table><tr><td>A</td></tr></table>",
    "<table>A<tr><td>B</td></tr>C</table>",
    "<p><b>One<i>Two</b>Three</i>",
    "A<!--comment & -->B",
    "<!--leading-->X",
    '<div class="retained"></div>',
    '<img src="x&y" alt=\'a"<>\'>',
    "<div><script>X</script></div>",
    "<div> </div>",
    "<div><!--x--></div>",
    "<noscript>X</noscript>Y",
    "<p><noscript>X</noscript>Y</p>",
    "<xmp><b>X</b></xmp>",
    "<plaintext><p>X</p>",
    "<textarea>&lt;b&gt;X</textarea>",
    "<p><svg><p>a</p></svg>z</p>",
    "<body>one</body>two",
    "<title>X</title>Y",
    "<p>\r\nX\rY\x00</p>",
    "<frameset><frame src=x></frameset>",
    "&nbsp;X&nbsp;",
    "&amp;nbsp;X&amp;nbsp;",
    "<p>&nbsp;X&nbsp;</p>",
]
for tag in sorted(_ALLOWED_TAGS | _DROP_SUBTREE_TAGS | {"div", "section", "input", "image", "wbr"}):
    cases.extend(
        [
            f'<{tag} class="x" onclick="y">text<p>child</p></{tag}>',
            f'<{tag} class="x"></{tag}>',
            f"&lt;{tag}&gt;X&lt;/{tag}&gt;",
            f"&LT;{tag.upper()}&GT;X&LT;/{tag.upper()}&GT;",
            f"&lt;{tag}_foo&gt;X&lt;/{tag}_foo&gt;",
        ]
    )
for entity in sorted(html.entities.html5):
    cases.extend([f"<p>&{entity}</p>", f"&lt;p&gt;&{entity}&lt;/p&gt;"])
points = (
    list(range(160))
    + list(range(0xFDD0, 0xFDF0))
    + [
        0xD800,
        0xDFFF,
        0x110000,
        999999999999999999,
    ]
    + [plane * 0x10000 + suffix for plane in range(17) for suffix in [0xFFFE, 0xFFFF]]
)
for cp in points:
    for ref in [f"&#{cp};", f"&#x{cp:x};", f"&#{cp}", f"&#x{cp:x}"]:
        cases.extend([f"<p>{ref}</p>", f"&lt;p&gt;{ref}&lt;/p&gt;"])
for tag in ["İ", "ı", "ſ", "K", "lİ", "lı", "blockquote", "pre", "br"]:
    for boundary in ["", "1", "_", "é", "漢", "-", "\u0301"]:
        cases.append(f"&lt;{tag}{boundary}&gt;X&lt;/{tag}{boundary}&gt;")
for cp in [0x1C, 0x85, 0xA0, 0x1680, 0x2000, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000]:
    cases.append(f"&lt;{chr(cp)}p&gt;X&lt;/p&gt;")

rng = random.Random(7966)
tags = sorted(
    _ALLOWED_TAGS
    | _DROP_SUBTREE_TAGS
    | {
        "div",
        "section",
        "table",
        "td",
        "tr",
        "button",
        "form",
        "body",
        "title",
        "select",
        "option",
        "textarea",
        "xmp",
        "plaintext",
    }
)
texts = [
    " A ",
    "é漢字",
    "\u00a0",
    "&lt;p&gt;X&lt;/p&gt;",
    "&amp;copy;",
    "<!-- Y -->",
    "\x00",
    "&quot;",
    "&nbsp;",
    "&gt;",
    "&#x80;",
]
for _ in range(2000):
    cases.append(
        "".join(
            rng.choice([f"<{rng.choice(tags)}>", f"</{rng.choice(tags)}>", rng.choice(texts)])
            for _ in range(rng.randrange(1, 25))
        )
    )

cases = list(dict.fromkeys(cases))
output = {
    "oracle": "src.shared.html_normalize._normalize_description_html_python; selectolax 0.4.11",
    "cases": [{"text": raw, "html": _normalize_description_html_python(raw)} for raw in cases],
}
Path(__file__).with_name("python_html.json").write_text(
    json.dumps(output, ensure_ascii=False, indent=2) + "\n"
)
print(f"Frozen {len(cases)} canonical HTML cases")
