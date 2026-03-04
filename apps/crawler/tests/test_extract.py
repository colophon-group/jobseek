from __future__ import annotations

import warnings

from src.shared.extract import (
    _join_html,
    _norm,
    extract_sections,
    flatten,
    walk_steps,
)


class TestFlatten:
    def test_basic_block_elements(self):
        html = "<p>Hello</p><div>World</div><h1>Title</h1>"
        els = flatten(html)
        assert len(els) == 3
        assert els[0]["tag"] == "p"
        assert els[0]["text"] == "Hello"
        assert els[1]["tag"] == "div"
        assert els[1]["text"] == "World"
        assert els[2]["tag"] == "h1"
        assert els[2]["text"] == "Title"

    def test_inline_tags_fold_into_parent(self):
        html = "<p>Hello <strong>bold</strong> and <a href='#'>link</a> text</p>"
        els = flatten(html)
        assert len(els) == 1
        assert els[0]["tag"] == "p"
        assert els[0]["text"] == "Hello bold and link text"

    def test_skip_tags_excluded(self):
        html = (
            "<div>Visible</div><script>var x=1;</script><style>.x{}</style><div>Also visible</div>"
        )
        els = flatten(html)
        assert len(els) == 2
        assert els[0]["text"] == "Visible"
        assert els[1]["text"] == "Also visible"

    def test_noise_tags_excluded(self):
        html = "<nav>Menu</nav><div>Content</div><footer>Footer</footer>"
        els = flatten(html)
        assert len(els) == 1
        assert els[0]["text"] == "Content"

    def test_aria_hidden_excluded(self):
        html = (
            '<div>Visible</div><div aria-hidden="true"><p>Hidden subtree</p></div><div>After</div>'
        )
        els = flatten(html)
        assert len(els) == 2
        assert els[0]["text"] == "Visible"
        assert els[1]["text"] == "After"

    def test_void_tags_dont_break_stack(self):
        html = "<p>Before<br>After</p>"
        els = flatten(html)
        assert len(els) == 1
        assert els[0]["text"] == "Before After"

    def test_whitespace_collapsed(self):
        html = "<p>  lots   of    spaces  </p>"
        els = flatten(html)
        assert len(els) == 1
        assert els[0]["text"] == "lots of spaces"

    def test_empty_text_no_elements(self):
        html = "<p>   </p><div></div>"
        els = flatten(html)
        assert len(els) == 0

    def test_nested_block_elements_flush(self):
        html = "<div><p>Inner para</p><p>Second para</p></div>"
        els = flatten(html)
        assert len(els) == 2
        assert els[0]["tag"] == "p"
        assert els[0]["text"] == "Inner para"
        assert els[1]["tag"] == "p"
        assert els[1]["text"] == "Second para"

    def test_attrs_populated(self):
        html = '<p class="intro" id="p1">Hello</p>'
        els = flatten(html)
        assert len(els) == 1
        assert els[0]["attrs"]["class"] == "intro"
        assert els[0]["attrs"]["id"] == "p1"

    def test_img_void_tag_in_paragraph(self):
        html = '<p>Text <img src="logo.png"> more text</p>'
        els = flatten(html)
        assert len(els) == 1
        assert els[0]["text"] == "Text more text"

    def test_hidden_attribute_excluded(self):
        html = "<div>Show</div><div hidden><p>Hidden</p></div><div>End</div>"
        els = flatten(html)
        assert len(els) == 2
        assert els[0]["text"] == "Show"
        assert els[1]["text"] == "End"


class TestNorm:
    def test_smart_quotes_to_ascii(self):
        assert _norm("\u2018hello\u2019") == "'hello'"
        assert _norm("\u201chello\u201d") == '"hello"'

    def test_dashes_normalized(self):
        assert _norm("a\u2013b") == "a-b"
        assert _norm("a\u2014b") == "a-b"

    def test_nonbreaking_space(self):
        assert _norm("hello\u00a0world") == "hello world"

    def test_zero_width_spaces(self):
        assert _norm("ab\u200bcd") == "abcd"
        assert _norm("ab\u200ccd") == "abcd"
        assert _norm("ab\u200dcd") == "abcd"
        assert _norm("ab\ufeffcd") == "abcd"

    def test_lowercased(self):
        assert _norm("Hello WORLD") == "hello world"


class TestJoinHtml:
    def test_p_elements_wrapped(self):
        els = [
            {"tag": "p", "text": "First"},
            {"tag": "p", "text": "Second"},
        ]
        assert _join_html(els) == "<p>First</p><p>Second</p>"

    def test_consecutive_li_grouped_in_ul(self):
        els = [
            {"tag": "li", "text": "One"},
            {"tag": "li", "text": "Two"},
        ]
        assert _join_html(els) == "<ul><li>One</li><li>Two</li></ul>"

    def test_mixed_li_and_p(self):
        els = [
            {"tag": "p", "text": "Intro"},
            {"tag": "li", "text": "A"},
            {"tag": "li", "text": "B"},
            {"tag": "p", "text": "End"},
        ]
        result = _join_html(els)
        assert result == "<p>Intro</p><ul><li>A</li><li>B</li></ul><p>End</p>"

    def test_text_html_escaped(self):
        els = [{"tag": "p", "text": "A & B < C > D 'E'"}]
        result = _join_html(els)
        assert "&amp;" in result
        assert "&lt;" in result
        assert "&gt;" in result
        assert "&#x27;" in result or "'" in result  # escape may vary


class TestWalkSteps:
    def _els(self, *items):
        """Build element list from (tag, text) tuples."""
        return [{"tag": t, "attrs": {}, "text": x} for t, x in items]

    def test_basic_single_match(self):
        els = self._els(("h1", "Title"), ("p", "Description"))
        steps = [{"tag": "h1", "field": "title"}]
        result = walk_steps(els, steps)
        assert result["title"] == "Title"

    def test_text_match(self):
        els = self._els(("p", "Location: Berlin"), ("p", "Other"))
        steps = [{"text": "Location", "field": "loc"}]
        result = walk_steps(els, steps)
        assert result["loc"] == "Location: Berlin"

    def test_range_with_stop(self):
        els = self._els(
            ("h2", "Description"),
            ("p", "Line 1"),
            ("p", "Line 2"),
            ("h2", "Requirements"),
            ("p", "Req 1"),
        )
        steps = [{"tag": "h2", "text": "Description", "field": "desc", "stop": "Requirements"}]
        result = walk_steps(els, steps)
        assert "Description" in result["desc"]
        assert "Line 1" in result["desc"]
        assert "Line 2" in result["desc"]
        assert "Requirements" not in result["desc"]

    def test_stop_tag(self):
        els = self._els(
            ("p", "Start"),
            ("p", "Middle"),
            ("h2", "Next Section"),
        )
        steps = [{"tag": "p", "text": "Start", "field": "section", "stop_tag": "h2"}]
        result = walk_steps(els, steps)
        assert "Start" in result["section"]
        assert "Middle" in result["section"]
        assert "Next Section" not in result["section"]

    def test_stop_count(self):
        els = self._els(
            ("p", "One"),
            ("p", "Two"),
            ("p", "Three"),
            ("p", "Four"),
        )
        steps = [{"tag": "p", "field": "first_two", "stop_count": 2}]
        result = walk_steps(els, steps)
        assert "One" in result["first_two"]
        assert "Two" in result["first_two"]
        assert "Three" not in result["first_two"]

    def test_optional_no_warning(self):
        els = self._els(("p", "Hello"))
        steps = [{"tag": "h1", "field": "missing", "optional": True}]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = walk_steps(els, steps)
            assert result["missing"] is None
            assert len(w) == 0

    def test_not_found_warns(self):
        els = self._els(("p", "Hello"))
        steps = [{"tag": "h1", "field": "missing"}]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = walk_steps(els, steps)
            assert result["missing"] is None
            assert len(w) == 1
            assert "not found" in str(w[0].message)

    def test_attr_key_value_match(self):
        els = [
            {"tag": "div", "attrs": {"class": "job-title"}, "text": "Engineer"},
            {"tag": "div", "attrs": {"class": "job-loc"}, "text": "Berlin"},
        ]
        steps = [{"attr": "class=job-loc", "field": "location"}]
        result = walk_steps(els, steps)
        assert result["location"] == "Berlin"

    def test_attr_key_exists_match(self):
        els = [
            {"tag": "div", "attrs": {}, "text": "No attr"},
            {"tag": "div", "attrs": {"data-role": "title"}, "text": "With attr"},
        ]
        steps = [{"attr": "data-role", "field": "val"}]
        result = walk_steps(els, steps)
        assert result["val"] == "With attr"

    def test_regex_capture(self):
        els = self._els(("p", "Location: Berlin, Germany"))
        steps = [{"tag": "p", "field": "city", "regex": r"Location:\s*(.+)"}]
        result = walk_steps(els, steps)
        assert result["city"] == "Berlin, Germany"

    def test_regex_no_match_keeps_original(self):
        els = self._els(("p", "Hello World"))
        steps = [{"tag": "p", "field": "val", "regex": r"Missing:\s*(.+)"}]
        result = walk_steps(els, steps)
        assert result["val"] == "Hello World"

    def test_split_produces_list(self):
        els = self._els(("p", "Python,Java,Go"))
        steps = [{"tag": "p", "field": "skills", "split": ","}]
        result = walk_steps(els, steps)
        assert result["skills"] == ["Python", "Java", "Go"]

    def test_split_filters_empties(self):
        els = self._els(("p", "A,,B, ,C"))
        steps = [{"tag": "p", "field": "items", "split": ","}]
        result = walk_steps(els, steps)
        assert result["items"] == ["A", "B", "C"]

    def test_offset_skips_elements(self):
        els = self._els(("h2", "Title"), ("p", "Subtitle"), ("p", "Content"))
        steps = [{"tag": "h2", "field": "val", "offset": 1}]
        result = walk_steps(els, steps)
        assert result["val"] == "Subtitle"

    def test_from_seeks_from_index(self):
        els = self._els(("p", "First"), ("p", "Second"), ("p", "Third"))
        # First step advances cursor past index 0
        steps = [
            {"tag": "p", "field": "a"},
            {"tag": "p", "field": "b", "from": 0},
        ]
        result = walk_steps(els, steps)
        assert result["a"] == "First"
        assert result["b"] == "First"  # "from": 0 overrides cursor

    def test_html_range_produces_html(self):
        els = [
            {"tag": "p", "attrs": {}, "text": "Intro"},
            {"tag": "li", "attrs": {}, "text": "Item 1"},
            {"tag": "li", "attrs": {}, "text": "Item 2"},
            {"tag": "h2", "attrs": {}, "text": "Next"},
        ]
        steps = [{"tag": "p", "text": "Intro", "field": "content", "stop": "Next", "html": True}]
        result = walk_steps(els, steps)
        assert "<p>Intro</p>" in result["content"]
        assert "<ul>" in result["content"]
        assert "<li>Item 1</li>" in result["content"]
        assert "<li>Item 2</li>" in result["content"]
        assert "</ul>" in result["content"]

    def test_return_contract_all_fields_present(self):
        els = self._els(("p", "Hello"))
        steps = [
            {"tag": "p", "field": "found"},
            {"tag": "h1", "field": "missing", "optional": True},
            {"tag": "h2", "field": "also_missing", "optional": True},
        ]
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = walk_steps(els, steps)
        assert "found" in result
        assert "missing" in result
        assert "also_missing" in result
        assert result["found"] == "Hello"
        assert result["missing"] is None
        assert result["also_missing"] is None

    def test_anchor_step_no_field(self):
        els = self._els(("h1", "Header"), ("p", "Para 1"), ("p", "Para 2"))
        steps = [
            {"tag": "h1"},  # anchor — no field
            {"tag": "p", "field": "val"},
        ]
        result = walk_steps(els, steps)
        assert "val" in result
        assert result["val"] == "Para 1"

    def test_unicode_text_matching(self):
        els = self._els(("p", "What\u2019s New"))
        steps = [{"text": "What's New", "field": "title"}]
        result = walk_steps(els, steps)
        assert result["title"] == "What\u2019s New"

    def test_case_insensitive_matching(self):
        els = self._els(("h2", "JOB DESCRIPTION"), ("p", "Details here"))
        steps = [{"text": "job description", "field": "heading"}]
        result = walk_steps(els, steps)
        assert result["heading"] == "JOB DESCRIPTION"

    def test_case_insensitive_stop(self):
        els = self._els(
            ("p", "Content"),
            ("h2", "REQUIREMENTS"),
        )
        steps = [{"tag": "p", "field": "section", "stop": "requirements"}]
        result = walk_steps(els, steps)
        assert result["section"] == "Content"


class TestExtractSections:
    def test_same_as_flatten_plus_walk(self):
        html = "<h1>Title</h1><p>Body text</p>"
        steps = [
            {"tag": "h1", "field": "title"},
            {"tag": "p", "field": "body"},
        ]
        result = extract_sections(html, steps)
        expected = walk_steps(flatten(html), steps)
        assert result == expected

    def test_end_to_end(self):
        html = """
        <div>
            <h1>Software Engineer</h1>
            <p>Location: Berlin</p>
            <h2>Description</h2>
            <p>We are looking for an engineer.</p>
            <p>You will build things.</p>
            <h2>Requirements</h2>
            <li>Python</li>
            <li>SQL</li>
        </div>
        """
        steps = [
            {"tag": "h1", "field": "title"},
            {"text": "Location", "field": "location", "regex": r"Location:\s*(.+)"},
            {"text": "Description", "field": "desc", "stop": "Requirements"},
            {"text": "Requirements", "field": "reqs", "stop_tag": "h2"},
        ]
        result = extract_sections(html, steps)
        assert result["title"] == "Software Engineer"
        assert result["location"] == "Berlin"
        assert "We are looking" in result["desc"]
        assert "Python" in result["reqs"]
