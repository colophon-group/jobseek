package queue

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestSharedDOMJSONLDDetailEnrichmentPreservesMaskAndFullBinding(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld"} {
		t.Run(scraper, func(t *testing.T) {
			c := domDetailConfig()
			options := map[string]any{}
			if scraper == "dom" {
				options["steps"] = []any{map[string]any{"tag": "h1", "field": "title"}}
			}
			inspect := InspectDOMDetail
			if scraper == "json-ld" {
				inspect = InspectJSONLDDetail
			}
			set := func() {
				body, err := json.Marshal(map[string]any{"scraper_type": scraper, "scraper_config": options})
				if err != nil {
					t.Fatal(err)
				}
				c["metadata"] = string(body)
			}
			set()
			plain, err := inspect(jsonldBoardID, c, "https://jobs.example.net/42", Simple)
			if err != nil || len(plain.EnrichmentFields) != 0 {
				t.Fatal("ordinary detail changed", err)
			}
			for field := range detailEnrichmentFields {
				options["enrich"] = []string{field}
				set()
				p, err := inspect(jsonldBoardID, c, plain.SourceURL, Simple)
				if err != nil || !reflect.DeepEqual(p.EnrichmentFields, []string{field}) || p.EffectiveBoardSHA256 == plain.EffectiveBoardSHA256 || p.Profile != plain.Profile {
					t.Fatal("selected enrichment or canonical binding changed", field, err)
				}
			}
			for _, bad := range []any{true, "description", []string{"unknown"}, []string{"language"}, []string{"extras"}, []string{"description", "description"}, []any{nil}} {
				options["enrich"] = bad
				set()
				if _, err := inspect(jsonldBoardID, c, plain.SourceURL, Simple); err == nil {
					t.Fatal("unqualified selected writer field admitted")
				}
			}
		})
	}
}
