from __future__ import annotations

from src.shared.html_normalize import normalize_description_html


class TestNormalizeDescriptionHtml:
    def test_none_and_blank(self):
        assert normalize_description_html(None) is None
        assert normalize_description_html("") is None
        assert normalize_description_html("   ") is None

    def test_decodes_escaped_html_and_drops_attributes(self):
        raw = (
            "&lt;p class=&quot;Lexical__paragraph&quot;&gt;"
            "Join Proton &amp;nbsp;"
            "&lt;a class=&quot;Lexical__link&quot; href=&quot;https://proton.me&quot;&gt;here&lt;/a&gt;"
            "&lt;/p&gt;"
        )
        cleaned = normalize_description_html(raw)
        assert cleaned is not None
        assert cleaned.startswith("<p>")
        assert "<a>here</a>" in cleaned
        assert "class=" not in cleaned
        assert "href=" not in cleaned

    def test_strips_attributes_from_raw_html(self):
        raw = (
            '<p class="copy"><strong data-x="1">Role</strong> '
            '<a href="https://example.com" target="_blank">apply</a></p>'
        )
        assert normalize_description_html(raw) == "<p><strong>Role</strong> <a>apply</a></p>"

    def test_removes_dangerous_subtrees(self):
        raw = "<p>Hello</p><script>alert(1)</script><p>World</p>"
        assert normalize_description_html(raw) == "<p>Hello</p><p>World</p>"

    def test_unwraps_unknown_tags(self):
        raw = "<div><section><p>One</p><p>Two</p></section></div>"
        assert normalize_description_html(raw) == "<p>One</p><p>Two</p>"

    def test_does_not_decode_non_markup_entities(self):
        raw = "Use &lt;script&gt;alert(1)&lt;/script&gt; as an example."
        expected = "Use &lt;script&gt;alert(1)&lt;/script&gt; as an example."
        assert normalize_description_html(raw) == expected
