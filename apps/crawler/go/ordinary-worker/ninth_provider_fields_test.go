package worker

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestNinthProviderFieldsMatchActualPython(t *testing.T) {
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_ninth_provider_core.json")
	if err != nil {
		t.Fatal(err)
	}
	d, err := api.Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	cases := d.Value.(map[string]any)["cases"].([]any)
	if len(cases) != 28 {
		t.Fatal("actual Python corpus changed")
	}
	for _, c := range cases {
		m := c.(map[string]any)
		t.Run(fmt.Sprint(m["provider"], "/", m["name"]), func(t *testing.T) {
			metadata, _ := json.Marshal(m["metadata"])
			o, err := api.NinthProviderOptionsFromMetadata(m["provider"].(string), m["source"].(string), string(metadata))
			if err != nil {
				t.Fatal(err)
			}
			fields, err := d.NinthProviderJobFields(m["row"], o)
			if fields != nil && err == nil {
				err = normalizeNinthFields(fields, m["row"].(map[string]any), o.Provider)
			}
			if m["error"] == true {
				if err == nil {
					t.Fatal("Python failure accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if fields != nil {
				for _, key := range []string{"url", "title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "extras", "metadata", "localizations"} {
					if _, ok := fields[key]; !ok {
						fields[key] = nil
					}
				}
			}
			canonical := func(v any) any {
				raw, err := json.Marshal(v)
				if err != nil {
					t.Fatal(err)
				}
				var out any
				if json.Unmarshal(raw, &out) != nil {
					t.Fatal("invalid fixture JSON")
				}
				return out
			}
			if !reflect.DeepEqual(canonical(fields), canonical(m["expected"])) {
				t.Fatalf("Python fields changed:\ngot %v\nwant %v", fields, m["expected"])
			}
		})
	}
}
