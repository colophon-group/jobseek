package apisniffer

import (
	"context"
	"encoding/json"
	"net/url"
	"os"
	"reflect"
	"testing"
)

func TestOriginalRootArrayHTTPInventory(t *testing.T) {
	testHTTPDiscoveryOracle(t, "testdata/python_api_root_inventory.json", true)
}

func TestOriginalRootArrayBrowserInventory(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_api_root_inventory.json")
	var cases []struct {
		Name, Board string
		BoardURL    string `json:"board_url"`
		Metadata    map[string]any
		Expected    []Job
		Responses   []struct{ Data json.RawMessage }
	}
	if e != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 8 {
		t.Fatal("original root-array oracle unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			c.Metadata["browser"] = true
			metadata, _ := json.Marshal(c.Metadata)
			o, e := BrowserReplayOptionsFromMetadata(c.BoardURL, string(metadata))
			if e != nil {
				t.Fatal(e)
			}
			calls := 0
			out, e := DiscoverBrowserReplay(context.Background(), o, func(_ context.Context, r Request) (*Document, error) {
				calls++
				if calls != 1 || r.URL != o.Inventory.Endpoint || r.Method != "GET" {
					t.Error("root replay issued a changed request")
				}
				return Decode(c.Responses[0].Data)
			}, func(base, ref string) (string, error) {
				b, e := url.Parse(base)
				if e != nil {
					return "", e
				}
				u, e := url.Parse(ref)
				if e != nil {
					return "", e
				}
				return b.ResolveReference(u).String(), nil
			}, false)
			if e != nil || out.Truncated || out.URLOnly || !reflect.DeepEqual(out.Jobs, c.Expected) || calls != 1 {
				t.Fatal("original root browser inventory differs", e)
			}
		})
	}
}
