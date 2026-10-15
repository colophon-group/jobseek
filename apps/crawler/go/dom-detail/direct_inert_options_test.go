package dom

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestDirectDOMIgnoresOriginalMapAndReplaceMarkers(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_inert_direct_options.json")
	var original struct {
		Source string `json:"source_revision"`
		HTML   string
		Cases  []struct {
			Config   json.RawMessage
			Expected map[string]any
		}
	}
	if e != nil || json.Unmarshal(raw, &original) != nil || original.Source != "f7de6c2d103e9120f8f4e7b104d72f6360f49027" || len(original.Cases) != 3 {
		t.Fatal("original fixture missing")
	}
	for _, c := range original.Cases {
		var config Object
		d := json.NewDecoder(bytes.NewReader(c.Config))
		d.UseNumber()
		if d.Decode(&config) != nil {
			t.Fatal("fixtureconfiginvalid")
		}
		calls := 0
		client := &http.Client{Transport: directRoundTrip(func(r *http.Request) (*http.Response, error) {
			calls++
			return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"text/html"}}, Body: io.NopCloser(strings.NewReader(original.HTML)), Request: r}, nil
		})}
		got, e := FetchDetailWithClient(context.Background(), "https://example.com/job", config, client)
		if e != nil || calls != 1 || !reflect.DeepEqual(got.Content, c.Expected) {
			t.Fatal("inert metadata changed original fields or transport", e)
		}
	}
}
