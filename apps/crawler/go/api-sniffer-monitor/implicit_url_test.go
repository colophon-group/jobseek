package apisniffer

import "testing"

func TestImplicitURLFieldPrioritiesAndSampleScope(t *testing.T) {
	for _, c := range []struct{ name, body, field string }{
		{"canonical-over-apply-artwork", `[{"pictureUrl":"https://example.com/logo.png","applyUrl":"https://example.com/apply/1","links":{"canonical":"/jobs/1"}}]`, "links.canonical"},
		{"posting-over-generic", `[{"url":"/generic/1","OpenAdvertUrl":"/jobs/1"}]`, "OpenAdvertUrl"},
		{"tie-in-original-order", `[{"firstUrl":"/jobs/1","secondUrl":"/other/1"}]`, "firstUrl"},
		{"top-level-fallback", `[{"unexpected":"/jobs/1","title":"Engineer"}]`, "unexpected"},
		{"nested-fallback-excluded", `[{"nested":{"unexpected":"/jobs/1"},"title":"Engineer"}]`, ""},
		{"artwork-excluded", `[{"imageUrl":"https://example.com/logo.png"}]`, ""},
		{"all-sample-values-required", `[{"canonical":"/jobs/1","url":"/other/1"},{"canonical":null,"url":"/other/2"}]`, "url"},
		{"quoted-key", `[{"job.url":"/jobs/1"}]`, `"job.url"`},
	} {
		t.Run(c.name, func(t *testing.T) {
			d, e := Decode([]byte(c.body))
			if e != nil {
				t.Fatal(e)
			}
			rows := []map[string]any{}
			for _, v := range d.Value.([]any) {
				rows = append(rows, v.(map[string]any))
			}
			if got := d.FindURLField(rows); got != c.field {
				t.Fatal("original URL priority changed", got, c.field)
			}
		})
	}
}

func TestExplicitAPIFieldListsKeepSeparateGenericGrammar(t *testing.T) {
	for _, c := range []struct {
		spec     any
		accepted bool
	}{
		{[]any{map[string]any{"path": "body", "html_unescape": true}, map[string]any{"path": "extra", "html_unescape": true}}, true},
		{[]any{"body", map[string]any{"each": "locations", "wrap": "<p>{item}</p>"}}, true},
		{[]any{map[string]any{"path": "body", "unknown": true}}, false},
		{[]any{map[string]any{"path": false}}, false},
	} {
		if (ValidateAPIField(c.spec) == nil) != c.accepted {
			t.Fatal("API field-list grammar changed", c.spec)
		}
	}
	if ValidateField([]any{map[string]any{"path": "body", "html_unescape": true}}) == nil {
		t.Fatal("API-only extension leaked into generic concatenation")
	}
}
