package apisniffer

import (
	"context"
	"os"
	"reflect"
	"testing"
)

func TestItemFiltersMatchActualPython(t *testing.T) {
	body, err := os.ReadFile("testdata/python_item_filters.json")
	if err != nil {
		t.Fatal(err)
	}
	document, err := Decode(body)
	if err != nil {
		t.Fatal(err)
	}
	cases := document.Value.([]any)
	if len(cases) != 29 {
		t.Fatal("Python item corpus changed", len(cases))
	}
	for _, raw := range cases {
		c := raw.(map[string]any)
		t.Run(c["name"].(string), func(t *testing.T) {
			rows := []map[string]any{}
			for _, row := range c["items"].([]any) {
				rows = append(rows, row.(map[string]any))
			}
			filter, err := ItemFilterOptions(c["config"])
			var indices []int
			if err == nil {
				indices, err = FilterItemIndices(rows, filter)
			}
			if c["error"] == true {
				if err == nil {
					t.Fatal("Python-invalid item filter accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			selected := []any{}
			for _, i := range indices {
				selected = append(selected, rows[i])
			}
			if !reflect.DeepEqual(selected, c["rows"]) {
				t.Fatal("item scope, identity or winner changed", selected, c["rows"])
			}
		})
	}
}

func TestItemScopePreservesWholeSourceGapAndLateFailure(t *testing.T) {
	for _, mode := range []string{"complete", "gap", "late-invalid", "non-object"} {
		t.Run(mode, func(t *testing.T) {
			o, err := OptionsFromMetadata("https://example.com/careers", `{"api_url":"https://example.com/api","json_path":"jobs","total_path":"total","url_template":"https://example.com/jobs/{id}","fields":{"title":"=Engineer"},"pagination":{"param_name":"page","start_value":1,"increment":1,"max_pages":2},"item_filter":{"include":{"scope":["main"]},"require_regex":{"id":"[0-9]+"}}}`)
			if err != nil {
				t.Fatal(err)
			}
			calls := 0
			fetch := func(context.Context, Request) (*Document, error) {
				calls++
				total := "4"
				if mode == "gap" {
					total = "10"
				}
				body := `{"total":` + total + `,"jobs":[{"id":"1","scope":"main"},{"id":"2","scope":"other"}]}`
				if calls == 2 {
					body = `{"jobs":[{"id":"3","scope":"main"},{"id":"4","scope":"other"}]}`
					if mode == "late-invalid" {
						body = `{"jobs":[{"id":"bad","scope":"main"},{"id":"4","scope":"other"}]}`
					}
				}
				if mode == "non-object" {
					body = `{"total":1,"jobs":[1]}`
				}
				return Decode([]byte(body))
			}
			inventory, err := Discover(context.Background(), o, fetch, PythonJoinURL)
			failure := mode == "late-invalid" || mode == "non-object"
			if failure {
				if err == nil || len(inventory.Jobs) != 0 {
					t.Fatal("invalid source published partial inventory", err, inventory)
				}
				return
			}
			if err != nil || calls != 2 || len(inventory.Jobs) != 2 || inventory.Truncated != (mode == "gap") {
				t.Fatal("intentional scope masked source gap", err, calls, inventory)
			}
		})
	}
}

func TestItemPreferenceChoosesLatePageWinnerBeforeProjection(t *testing.T) {
	o, err := OptionsFromMetadata("https://example.com/careers", `{"api_url":"https://example.com/api","json_path":"jobs","total_path":"total","url_field":"url","fields":{"title":"title"},"pagination":{"param_name":"page","start_value":1,"increment":1,"max_pages":2},"item_filter":{"dedupe_by":["id"],"dedupe_preference":{"path":"locale","preferred_values":["en","fr"],"fallback_by":["locale"]}}}`)
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	got, err := Discover(context.Background(), o, func(context.Context, Request) (*Document, error) {
		calls++
		if calls == 1 {
			return Decode([]byte(`{"total":3,"jobs":[{"id":"1","locale":"fr","url":"https://example.com/jobs/un","title":"French"},{"id":"2","locale":"fr","url":"https://example.com/jobs/deux","title":"Second"}]}`))
		}
		return Decode([]byte(`{"jobs":[{"id":"1","locale":"en","url":"https://example.com/jobs/one","title":"English"}]}`))
	}, PythonJoinURL)
	if err != nil || calls != 2 || got.Truncated || len(got.Jobs) != 2 || got.Jobs[0].URL != "https://example.com/jobs/deux" || got.Jobs[1].URL != "https://example.com/jobs/one" || got.Jobs[1].Title != "English" {
		t.Fatal("global preference projected first-page loser or hid source gap", err, calls, got)
	}
}
