package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"testing"
)

func TestOriginalPythonAbsentBodyPagination(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_absent_body_pagination.json")
	var cases []struct {
		Name      string
		Metadata  json.RawMessage
		Requests  []struct{ Method, URL, Body string }
		Responses []json.RawMessage
		Jobs      []Job
		Truncated bool
	}
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 4 {
		t.Fatal("original absent-body fixture unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, err := OptionsFromMetadata("https://example.com/careers", string(c.Metadata))
			if err != nil {
				t.Fatal(err)
			}
			n := 0
			out, err := Discover(context.Background(), o, func(_ context.Context, r Request) (*Document, error) {
				if n >= len(c.Requests) {
					t.Fatal("extra request")
				}
				want := c.Requests[n]
				if r.Method != want.Method || r.URL != want.URL || r.Body != want.Body {
					t.Fatal("original method, URL or absent body changed")
				}
				response := c.Responses[n]
				n++
				return Decode(response)
			}, func(_, raw string) (string, error) { return raw, nil })
			if err != nil || n != len(c.Requests) || out.Truncated != c.Truncated {
				t.Fatal("original request/total authority changed", err, n, out.Truncated)
			}
			// Original discovery coalesces repeated source URLs after paging.
			urls := map[string]bool{}
			for _, j := range out.Jobs {
				urls[j.URL] = true
			}
			if len(urls) != len(c.Jobs) {
				t.Fatal("original complete URL inventory changed")
			}
			for _, j := range c.Jobs {
				if !urls[j.URL] {
					t.Fatal("original URL missing")
				}
			}
		})
	}
}

func TestAbsentBodyPaginationPreservesOriginalWireRequest(t *testing.T) {
	for _, total := range []string{"1", "20"} {
		md := `{"api_url":"https://example.com/api?company=example","method":"POST","json_path":"jobs","total_path":"total","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"pagination":{"style":"offset","location":"body","increment":9,"param_name":"offset","start_value":0,"max_pages":3}}`
		o, err := OptionsFromMetadata("https://example.com/careers", md)
		if err != nil {
			t.Fatal("original absent-body paginator rejected", err)
		}
		calls := 0
		out, err := Discover(context.Background(), o, func(_ context.Context, r Request) (*Document, error) {
			calls++
			if r.Method != "POST" || r.URL != o.Endpoint || r.Body != "" {
				t.Fatal("absent body became an invented pagination parameter")
			}
			return Decode([]byte(`{"total":` + total + `,"jobs":[{"id":"one","title":"Engineer"}]}`))
		}, func(_, raw string) (string, error) { return raw, nil })
		wantJobs := 1
		if total == "20" {
			wantJobs = 3 // Original source rows remain available on truncation.
		}
		if err != nil || len(out.Jobs) != wantJobs || calls < 1 || calls > 3 || out.Truncated != (total == "20") {
			t.Fatal("inventory total/truncation authority changed", err, calls, len(out.Jobs), out.Truncated)
		}
	}
	// Token refresh requires a real captured field. Permitting an absent
	// pagination body must not create an absent-body refresh capability.
	if _, err := OptionsFromMetadata("https://example.com/careers", `{"api_url":"https://example.com/api","method":"POST","json_path":"jobs","post_data_refresh":{"fields":{"nonce":"nonce=([a-z]+)"}}}`); err == nil {
		t.Fatal("absent POST refresh admitted")
	}
}
