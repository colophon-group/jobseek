package smartrecruiters

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"sync"
	"testing"
	"time"
)

func TestPythonInventoryParity(t *testing.T) {
	body, err := os.ReadFile("testdata/python_inventory.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures []struct {
		Name      string              `json:"name"`
		BoardURL  string              `json:"board_url"`
		Metadata  Object              `json:"metadata"`
		Responses map[string][]Object `json:"responses"`
		Expected  json.RawMessage     `json:"expected"`
		Error     string              `json:"error"`
		Requests  map[string]int      `json:"python_requests"`
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err = decoder.Decode(&fixtures); err != nil {
		t.Fatal(err)
	}
	for _, f := range fixtures {
		t.Run(f.Name, func(t *testing.T) {
			opt, err := OptionsFromMetadata(f.BoardURL, f.Metadata)
			var result Inventory
			counts := map[string]int{}
			var mu sync.Mutex
			get := func(ctx context.Context, endpoint string, limit int) (Object, error) {
				mu.Lock()
				defer mu.Unlock()
				if ctx.Err() != nil {
					return nil, ctx.Err()
				}
				values := f.Responses[endpoint]
				if len(values) == 0 {
					return nil, fmt.Errorf("unexpected offline endpoint %s", endpoint)
				}
				index := counts[endpoint]
				counts[endpoint]++
				if index >= len(values) {
					index = len(values) - 1
				}
				return values[index], nil
			}
			if err == nil {
				result, err = Discover(context.Background(), opt, get, func(context.Context, time.Duration) error { return nil })
			}
			if f.Error != "" {
				if err == nil {
					t.Fatalf("Python failed with %s; Go succeeded: %+v", f.Error, result)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			actual, _ := json.Marshal(Object{"urls": result.URLs, "jobs": result.Jobs, "truncated": result.Truncated})
			var a, b any
			da := json.NewDecoder(bytes.NewReader(actual))
			da.UseNumber()
			if err := da.Decode(&a); err != nil {
				t.Fatal(err)
			}
			db := json.NewDecoder(bytes.NewReader(f.Expected))
			db.UseNumber()
			if err := db.Decode(&b); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(a, b) {
				t.Fatalf("output mismatch\nGo: %s\nPython: %s", actual, f.Expected)
			}
			if !reflect.DeepEqual(counts, f.Requests) {
				t.Fatalf("request mismatch Go=%v Python=%v", counts, f.Requests)
			}
		})
	}
}
