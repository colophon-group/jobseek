package worker

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestComposedArrayAndURLInferenceMatchesOriginalPythonFields(t *testing.T) {
	var cases []struct {
		Name              string
		Metadata, Payload json.RawMessage
		Expected          []api.Job
	}
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_implicit_path_url.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 5 {
		t.Fatal("original composed-inference corpus unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, err := api.OptionsFromMetadata("https://example.com/careers", string(c.Metadata))
			if err != nil || !o.AutoPath || !o.AutoURLField || o.AutoFields {
				t.Fatal("explicit fields or composed inference changed", err)
			}
			calls := 0
			got, err := api.Discover(context.Background(), o, func(_ context.Context, r api.Request) (*api.Document, error) {
				calls++
				if r.Method != "GET" || r.URL != o.Endpoint || r.Body != "" {
					t.Fatal("undeclared inferred request")
				}
				return api.Decode(c.Payload)
			}, func(_, source string) (string, error) { return source, nil })
			if err != nil || got.Truncated || calls != 1 || len(got.Jobs) != len(c.Expected) {
				t.Fatal("original complete inventory changed", err)
			}
			for i := range got.Jobs {
				got.Jobs[i].BaseSalary, err = normalizeDiscoveredSalary(got.Jobs[i].BaseSalary)
				if err != nil {
					t.Fatal("shared canonical salary preparation failed")
				}
				for _, job := range []*api.Job{&got.Jobs[i], &c.Expected[i]} {
					if job.Metadata == nil {
						job.Metadata = map[string]any{}
					}
					if job.Extras == nil {
						job.Extras = map[string]any{}
					}
				}
			}
			if !reflect.DeepEqual(got.Jobs, c.Expected) {
				t.Fatal("original fields, salary or inferred URLs differ")
			}
		})
	}
}
