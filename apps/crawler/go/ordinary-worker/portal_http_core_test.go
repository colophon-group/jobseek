package worker

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

func comparePortalOracle(want, got any, path string) []string {
	changed := []string{}
	switch w := want.(type) {
	case map[string]any:
		g, ok := got.(map[string]any)
		if !ok {
			return []string{path}
		}
		for key, value := range w {
			actual := g[key]
			if key == "extras" || key == "metadata" {
				if m, ok := value.(map[string]any); ok && len(m) == 0 {
					value = nil
				}
				if m, ok := actual.(map[string]any); ok && len(m) == 0 {
					actual = nil
				}
			}
			changed = append(changed, comparePortalOracle(value, actual, path+"."+key)...)
		}
	case []any:
		g, ok := got.([]any)
		if !ok || len(w) != len(g) {
			return []string{path}
		}
		for index, value := range w {
			changed = append(changed, comparePortalOracle(value, g[index], fmt.Sprintf("%s[%d]", path, index))...)
		}
	default:
		if !reflect.DeepEqual(want, got) {
			changed = append(changed, path)
		}
	}
	return changed
}

func TestPortalHTTPCoreMatchesActualOriginalPython(t *testing.T) {
	body, err := os.ReadFile("../api-sniffer-monitor/testdata/python_portal_http_core.json")
	if err != nil {
		t.Fatal("original portal corpus unavailable", err)
	}
	var cases []struct {
		Provider, Operation, Name, Status string
		Inputs                            map[string]json.RawMessage
		Expected                          json.RawMessage
	}
	if json.Unmarshal(body, &cases) != nil || len(cases) != 55 {
		t.Fatal("four-provider original corpus invalid")
	}
	for _, c := range cases {
		t.Run(c.Provider+"/"+c.Operation+"/"+c.Name, func(t *testing.T) {
			text := func(key string) string {
				var value string
				if raw, present := c.Inputs[key]; present && json.Unmarshal(raw, &value) != nil {
					t.Fatal("original text input invalid", key)
				}
				return value
			}
			var actual any
			var failure error
			switch c.Provider {
			case "keka", "turbohire":
				document, err := api.Decode(c.Inputs["row"])
				if err != nil {
					t.Fatal("original record invalid", err)
				}
				if c.Provider == "keka" {
					listing := "https://" + text("tenant") + ".keka.com/careers"
					if text("portal") != "default" {
						listing += "/" + text("portal")
					}
					actual, failure = api.KekaJobFields(document.Value, listing, enrichment.NormalizeDescriptionHTML)
				} else {
					actual, failure = api.TurboHireJobFields(document.Value.(map[string]any), text("origin"), enrichment.NormalizeDescriptionHTML)
				}
			case "infoniqa":
				if c.Operation == "shell" {
					actual, failure = api.ParseInfoniqaShell([]byte(text("body")), text("board_url"), text("employer"))
				} else {
					actual, failure = api.ParseInfoniqaSearch([]byte(text("body")), text("board_url"))
				}
			case "pageup":
				var instance, page, size int
				var expected *int
				if json.Unmarshal(c.Inputs["instance"], &instance) != nil || json.Unmarshal(c.Inputs["page"], &page) != nil || json.Unmarshal(c.Inputs["page_size"], &size) != nil || json.Unmarshal(c.Inputs["expected_total"], &expected) != nil {
					t.Fatal("original pagination input invalid")
				}
				board := api.PageUpBoard{Instance: instance, Pointer: text("source_pointer"), Locale: text("locale")}
				found, err := api.ParsePageUpListing([]byte(text("body")), board.PageURL(page, size), board, page, size, expected)
				failure = err
				actual = []any{found.Jobs, found.Total, found.HasNext}
			default:
				t.Fatal("unknown original provider", c.Provider)
			}
			if c.Status == "failed" {
				if failure == nil {
					t.Fatal("original invalid response was accepted")
				}
				return
			}
			if c.Status != "complete" || failure != nil {
				t.Fatal("original complete response failed", failure)
			}
			wire, err := json.Marshal(actual)
			if err != nil {
				t.Fatal("native output invalid", err)
			}
			var want, got any
			if json.Unmarshal(c.Expected, &want) != nil || json.Unmarshal(wire, &got) != nil {
				t.Fatal("original/native comparison invalid")
			}
			if changed := comparePortalOracle(want, got, "result"); len(changed) != 0 {
				t.Fatal("original populated field changed", changed)
			}
		})
	}
}
