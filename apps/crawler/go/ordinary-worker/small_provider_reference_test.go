package worker

import (
	"context"
	"encoding/json"
	"net/url"
	"os"
	"reflect"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

type smallProviderOracle struct {
	Provider, Name string
	Statuses       []int
	Board          struct {
		URL      string `json:"board_url"`
		Metadata map[string]any
	}
	Pages    []json.RawMessage
	Requests []struct {
		Method, URL string
		Headers     map[string]string
	}
	Error    bool
	Expected *struct {
		Jobs      []map[string]any
		Truncated bool
	}
}

func smallProviderOracleCases(t *testing.T) []smallProviderOracle {
	t.Helper()
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_small_provider_core.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []smallProviderOracle
	if json.Unmarshal(raw, &cases) != nil {
		t.Fatal("malformed original oracle")
	}
	return cases
}
func TestSmallProvidersOriginalPythonInventory(t *testing.T) {
	keys := []string{"url", "title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "localizations", "extras", "metadata", "source_identity"}
	for _, c := range smallProviderOracleCases(t) {
		if c.Statuses != nil {
			continue
		}
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			md, _ := json.Marshal(c.Board.Metadata)
			o, e := api.SmallProviderOptionsFromMetadata(c.Provider, c.Board.URL, string(md))
			if e != nil {
				t.Fatal(e)
			}
			calls := 0
			fetch := func(ctx context.Context, r api.Request) ([]byte, error) {
				if r.Method != "GET" || !o.ResourceMatches(r.URL) {
					t.Fatal("unbound request")
				}
				if calls < len(c.Requests) {
					expected := c.Requests[calls]
					u, _ := url.Parse(r.URL)
					want, _ := url.Parse(expected.URL)
					if r.Method != expected.Method || u.Scheme != want.Scheme || u.Host != want.Host || u.Path != want.Path || !reflect.DeepEqual(u.Query(), want.Query()) {
						t.Fatal("request identity differs")
					}
					for k, v := range expected.Headers {
						if r.Headers.Get(k) != v {
							t.Fatal("header differs", k)
						}
					}
				}
				body := c.Pages[min(calls, len(c.Pages)-1)]
				calls++
				return body, nil
			}
			fields, truncated, e := api.DiscoverSmallProvider(context.Background(), o, fetch, enrichment.NormalizeDescriptionHTML)
			if (e != nil) != c.Error {
				t.Fatal("original outcome differs", e, c.Error)
			}
			if c.Error {
				return
			}
			if truncated != c.Expected.Truncated || len(fields) != len(c.Expected.Jobs) {
				t.Fatal("inventory/truncation differs")
			}
			for _, field := range fields {
				if c.Provider == "seamlesshiring" {
					v, _ := field["job_location_type"].(string)
					field["job_location_type"] = nil
					if v = enrichment.NormalizeJobLocationType(v); v != "" {
						field["job_location_type"] = v
					}
				}
				for _, key := range keys {
					if _, ok := field[key]; !ok {
						field[key] = nil
					}
				}
			}
			raw, _ := json.Marshal(fields)
			var normalized []map[string]any
			if json.Unmarshal(raw, &normalized) != nil {
				t.Fatal("field JSON")
			}
			if !reflect.DeepEqual(normalized, c.Expected.Jobs) {
				t.Fatalf("fields differ: got=%v expected=%v", normalized, c.Expected.Jobs)
			}
		})
	}
}
