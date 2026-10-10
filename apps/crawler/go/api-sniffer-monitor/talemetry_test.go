package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"
)

func TestTalemetryOriginalInventories(t *testing.T) {
	body, e := os.ReadFile("testdata/python_talemetry.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name      string          `json:"name"`
		Board     string          `json:"board_url"`
		Metadata  json.RawMessage `json:"metadata"`
		Responses []string        `json:"responses"`
		Requests  []struct {
			URL     string            `json:"url"`
			Headers map[string]string `json:"headers"`
		} `json:"requests"`
		URLs  []string `json:"urls"`
		Error bool     `json:"error"`
	}
	if e = json.Unmarshal(body, &cases); e != nil {
		t.Fatal(e)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, e := TalemetryOptionsFromMetadata(c.Board, string(c.Metadata))
			if e != nil {
				t.Fatal(e)
			}
			n, waits := 0, 0
			fetch := func(ctx context.Context, r Request) ([]byte, error) {
				if n >= len(c.Requests) || n >= len(c.Responses) {
					t.Fatalf("unexpected request %d", n)
				}
				want := c.Requests[n]
				if r.Method != "GET" || r.Body != "" || r.URL != want.URL || !o.ResourceMatches(r.URL) {
					t.Fatalf("request %d differs: %s", n, r.URL)
				}
				for k, v := range want.Headers {
					if r.Headers.Get(k) != v {
						t.Fatalf("header %s differs", k)
					}
				}
				response := c.Responses[n]
				n++
				return []byte(response), nil
			}
			urls, e := DiscoverTalemetry(context.Background(), o, fetch, func(ctx context.Context, d time.Duration) error {
				if d != time.Second {
					t.Fatal("snapshot delay differs")
				}
				waits++
				return nil
			})
			if (e != nil) != c.Error {
				t.Fatalf("error parity: %v", e)
			}
			if !c.Error && !reflect.DeepEqual(urls, c.URLs) {
				t.Fatalf("inventory differs: %v", urls)
			}
			if n != len(c.Requests) || waits > 1 {
				t.Fatalf("request/retry count differs %d %d", n, waits)
			}
		})
	}
}

func TestTalemetryScopeAndCancellation(t *testing.T) {
	o, e := TalemetryOptionsFromMetadata("https://parkercareers.ttcportals.com/search/jobs", `{"transport":"jobs_json"}`)
	if e != nil {
		t.Fatal(e)
	}
	for _, raw := range []string{"https://foreign.example/search/jobs.json?page=1", o.PageURL(1) + "&extra=1", o.PageURL(1) + "#x", "https://parkercareers.ttcportals.com/jobs/1-role"} {
		if o.ResourceMatches(raw) {
			t.Fatal("scope escaped")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	calls := 0
	_, e = DiscoverTalemetry(ctx, o, func(ctx context.Context, r Request) ([]byte, error) { calls++; return nil, ctx.Err() }, func(context.Context, time.Duration) error { t.Fatal("cancellation retried"); return nil })
	if e != context.Canceled || calls != 1 {
		t.Fatalf("cancellation differs: %v", e)
	}
}
