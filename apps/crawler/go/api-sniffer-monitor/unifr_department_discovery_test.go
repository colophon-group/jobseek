package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"
)

func TestUnifrDepartmentDuplicateProofsMatchActualPython(t *testing.T) {
	var cases []struct {
		Source, Name string
		Responses    map[string]string
		Output       []map[string]any
		Error        bool
	}
	raw, err := os.ReadFile("testdata/python_unifr_department_discovery.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 6 {
		t.Fatal("department discovery corpus missing", err)
	}
	for _, c := range cases {
		t.Run(c.Source+"/"+c.Name, func(t *testing.T) {
			var sources map[string]UnifrOptions
			json.Unmarshal(unifrSourceContracts, &sources)
			o, err := UnifrOptionsFromMetadata(sources[c.Source].URL, `{"source":"`+c.Source+`"}`)
			if err != nil {
				t.Fatal(err)
			}
			out, err := DiscoverUnifr(context.Background(), o, time.Date(2026, 8, 26, 0, 0, 0, 0, time.UTC), func(_ context.Context, resource, kind string) ([]byte, error) {
				body, ok := c.Responses[resource]
				if !ok || kind != "html" {
					t.Error("unbound departmental request", resource)
					return nil, ErrInventory
				}
				return []byte(body), nil
			})
			if (err != nil) != c.Error || err != nil && len(out) != 0 {
				t.Fatal(err, c.Error, out)
			}
			if c.Error {
				return
			}
			if len(out) != len(c.Output) {
				t.Fatal(out, c.Output)
			}
			for i, job := range out {
				b, _ := json.Marshal(job)
				var actual map[string]any
				json.Unmarshal(b, &actual)
				for _, key := range []string{"url", "title", "description", "locations", "metadata", "extras"} {
					if !reflect.DeepEqual(actual[key], c.Output[i][key]) {
						t.Fatal(key, actual[key], c.Output[i][key])
					}
				}
			}
		})
	}
}
