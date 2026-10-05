package executor

import (
	"reflect"
	"strings"
	"testing"
)

func TestScrapedValuesEnrichBeforeMissingDefaultsWithoutMutatingInput(t *testing.T) {
	in := map[string]any{"title": "Engineer", "description": nil, "locations": []any{}, "extras": map[string]any{"responsibilities": []any{"Build reliable services."}}}
	defaults := map[string]any{"description": "default body", "locations": []any{"Zurich"}, "employment_type": "Full-time"}
	got, err := PrepareScrapedValues(in, defaults)
	if err != nil || !strings.Contains(got["description"].(string), "Build reliable services.") || strings.Contains(got["description"].(string), "default body") || !reflect.DeepEqual(got["locations"], defaults["locations"]) || got["employment_type"] != "Full-time" {
		t.Fatal("scrape enrichment/default ordering differs", got, err)
	}
	if in["description"] != nil || len(in["locations"].([]any)) != 0 {
		t.Fatal("scrape input mutated")
	}
	got, err = PrepareScrapedValues(map[string]any{"description": "", "locations": []any{"Paris"}}, defaults)
	if err != nil || got["description"] != "" || !reflect.DeepEqual(got["locations"], []any{"Paris"}) {
		t.Fatal("default replaced non-null scalar or nonempty list", got, err)
	}
}
