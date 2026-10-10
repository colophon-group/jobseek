package apisniffer

import (
	"bytes"
	"context"
	"encoding/json"
	"net/url"
	"os"
	"reflect"
	"sort"
	"testing"
)

func TestOriginalPythonAPIConvergenceOracle(t *testing.T) {
	data, err := os.ReadFile("testdata/python_pagination_convergence.json")
	var cases []struct {
		Name      string
		BoardURL  string `json:"board_url"`
		Metadata  json.RawMessage
		Responses []json.RawMessage
		Requests  []struct{ Method, URL, Body string }
		Jobs      []Job
		Truncated bool
		Error     bool
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	if err != nil || d.Decode(&cases) != nil || len(cases) != 14 {
		t.Fatal("unchanged original convergence oracle unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, err := OptionsFromMetadata(c.BoardURL, string(c.Metadata))
			if err != nil {
				t.Fatal("original valid configuration rejected", err)
			}
			at := 0
			fetch := func(_ context.Context, r Request) (*Document, error) {
				if at >= len(c.Requests) {
					t.Fatal("unsolicited convergence request")
				}
				want := c.Requests[at]
				body := c.Responses[at]
				at++
				u, e := url.Parse(r.URL)
				v, f := url.Parse(want.URL)
				if e != nil || f != nil || u.Host != v.Host || u.Path != v.Path || !reflect.DeepEqual(u.Query(), v.Query()) || r.Method != want.Method || r.Body != want.Body {
					t.Fatal("original request changed", at-1)
				}
				return Decode(body)
			}
			join := func(base, ref string) (string, error) {
				u, _ := url.Parse(base)
				v, err := url.Parse(ref)
				if err != nil {
					return "", err
				}
				return u.ResolveReference(v).String(), nil
			}
			actual, err := Discover(context.Background(), o, fetch, join)
			if (err != nil) != c.Error || at != len(c.Requests) || actual.Truncated != c.Truncated {
				t.Fatal("original outcome/request/truncation changed", err, at, len(c.Requests), actual.Truncated, c.Truncated)
			}
			sort.Slice(actual.Jobs, func(i, j int) bool { return actual.Jobs[i].URL < actual.Jobs[j].URL })
			for i := range c.Jobs {
				if c.Jobs[i].Metadata == nil {
					c.Jobs[i].Metadata = map[string]any{}
				}
				if c.Jobs[i].Extras == nil {
					c.Jobs[i].Extras = map[string]any{}
				}
			}
			if !reflect.DeepEqual(actual.Jobs, c.Jobs) {
				t.Fatal("original fields or retained prefix changed", len(actual.Jobs), len(c.Jobs))
			}
		})
	}
}
