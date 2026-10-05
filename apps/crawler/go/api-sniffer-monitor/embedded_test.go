package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestEmbeddedMatchesReferenceScrapers(t *testing.T) {
	body, err := os.ReadFile("testdata/python_embedded.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, HTML string
		Config     map[string]any
		Nextdata   bool
		Expected   map[string]any
	}
	if json.Unmarshal(body, &cases) != nil {
		t.Fatal("invalid frozen corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			got, err := ProjectEmbeddedDetail(c.HTML, c.Config, c.Nextdata)
			if len(c.Expected) == 0 {
				if err == nil && len(got) > 0 {
					t.Fatal("invalid embedded document produced content")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			raw, _ := json.Marshal(got)
			var normalized map[string]any
			json.Unmarshal(raw, &normalized)
			if !reflect.DeepEqual(normalized, c.Expected) {
				t.Fatalf("embedded reference differs\ngot %s\nwant %v", raw, c.Expected)
			}
		})
	}
}

func TestEmbeddedDefaultsAndAdmissionBoundaries(t *testing.T) {
	c := map[string]any{"fields": map[string]any{"title": "title", "description": "description", "locations": "locations"}, "defaults": map[string]any{"locations": []any{"Zurich"}, "description": "fallback"}}
	got, err := ProjectEmbeddedDetail(`<script id="__NEXT_DATA__">{"title":"Engineer","description":"","locations":[]}</script>`, c, true)
	if err != nil || got["description"] != "" || got["locations"] != nil {
		t.Fatal("pure parser applied processing defaults", got, err)
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "unknown"} {
		bad := map[string]any{"fields": c["fields"], key: true}
		if ValidateEmbeddedDetail(bad, true) == nil {
			t.Fatal("unsupported pipeline admitted", key)
		}
	}
	for _, bad := range []map[string]any{{"fields": map[string]any{}}, {"fields": c["fields"], "path": "["}, {"fields": c["fields"], "pattern": "["}, {"fields": c["fields"], "enrich": []any{"description", "description"}}, {"fields": c["fields"], "source": "foreign"}} {
		if ValidateEmbeddedDetail(bad, true) == nil {
			t.Fatal("invalid embedded config admitted")
		}
	}
	if _, err := ProjectEmbeddedDetail(strings.Repeat("x", (16<<20)+1), c, true); err == nil {
		t.Fatal("unbounded embedded document admitted")
	}
}
