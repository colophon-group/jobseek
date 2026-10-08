package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"sync"
	"testing"
	"time"
)

func TestUnifrCompleteLocaleUnionAndLateFailuresMatchActualPython(t *testing.T) {
	var cases []struct {
		Name, Today string
		Responses   map[string]string
		Output      []map[string]any
		Error       bool
	}
	raw, err := os.ReadFile("testdata/python_unifr_discovery.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 4 {
		t.Fatal("actual Python discovery reference missing", err)
	}
	o, err := UnifrOptionsFromMetadata(UnifrCentralFR, `{"source":"central"}`)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			today, err := time.Parse("2006-01-02", c.Today)
			if err != nil {
				t.Fatal(err)
			}
			var lock sync.Mutex
			calls := map[string]int{}
			jobs, err := DiscoverUnifr(context.Background(), o, today, func(ctx context.Context, resource, kind string) ([]byte, error) {
				lock.Lock()
				defer lock.Unlock()
				calls[resource]++
				body, exists := c.Responses[resource]
				if !exists || !o.ResourceMatches(resource) {
					t.Error("unexpected resource", resource)
					return nil, ErrInventory
				}
				return []byte(body), nil
			})
			if (err != nil) != c.Error || err != nil && len(jobs) != 0 {
				t.Fatal("failed inventory exposed a prefix", jobs, err)
			}
			if c.Error {
				return
			}
			if len(calls) != len(c.Responses) {
				t.Fatal(calls)
			}
			for _, count := range calls {
				if count != 1 {
					t.Fatal(calls)
				}
			}
			if len(jobs) != len(c.Output) {
				t.Fatal(jobs, c.Output)
			}
			for i, job := range jobs {
				body, _ := json.Marshal(job)
				var actual map[string]any
				if json.Unmarshal(body, &actual) != nil {
					t.Fatal("fields")
				}
				for _, key := range []string{"url", "title", "description", "locations", "date_posted", "language", "localizations", "extras", "metadata"} {
					if !reflect.DeepEqual(actual[key], c.Output[i][key]) {
						t.Fatal(key, actual[key], c.Output[i][key])
					}
				}
			}
		})
	}
}
